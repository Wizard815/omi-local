"""One user's CalDAV sync (routers/caldav.py runs it on demand, the local
scheduler every hour).

Tasks (VTODO, ``tasks_href``)
  push: every active action item and the ones completed in the last 30 days
        become ``omi-task-<id>`` VTODOs. An item is PUT only when its rendered
        text changed since the last push (Redis hash of digests), so an hourly
        tick costs nothing when nothing moved.
  pull: a VTODO the phone or desktop client toggled flips the Omi item the
        same way, once per remote change (the remote LAST-MODIFIED is
        remembered). VTODOs created outside Omi are imported once as action
        items (source "caldav") and then kept in step the same way.

Calendar (VEVENT, ``calendar_href``)
  push: day summaries as all-day events (``omi-day-<date>``), the events the
        pipeline extracted from conversations (``omi-event-<conversation>-<n>``)
        and, when asked, the conversations themselves as time blocks.
  pull: the calendar's events for the last week and the next 30 days land in
        the device-calendar snapshot (source "caldav"), where the chat's
        calendar tool and the app already look.

Nothing here raises past ``sync_user``: it returns a report, and the caller
stores the report or the error on the settings document.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import database.action_items as action_items_db
import database.caldav_settings as settings_db
import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.device_calendar as device_calendar_db
import database.redis_db as redis_db
from utils.caldav import ical
from utils.caldav.client import CalDavClient, CalDavError, Calendar, object_href

logger = logging.getLogger(__name__)

TASK_WINDOW_DAYS = 30
EVENT_PUSH_WINDOW_DAYS = 14
EVENT_PULL_PAST_DAYS = 7
EVENT_PULL_FUTURE_DAYS = 30
MAX_TASKS = 500
DEFAULT_CALENDAR_NAME = 'Omi'
DEFAULT_TASKS_NAME = 'Omi Tasks'


@dataclass
class SyncReport:
    tasks_pushed: int = 0
    tasks_pulled: int = 0
    tasks_imported: int = 0
    events_pushed: int = 0
    events_pulled: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            'tasks_pushed': self.tasks_pushed,
            'tasks_pulled': self.tasks_pulled,
            'tasks_imported': self.tasks_imported,
            'events_pushed': self.events_pushed,
            'events_pulled': self.events_pulled,
            'errors': list(self.errors),
        }


# ---- defaults for the bundled server ------------------------------------------------------


def default_settings() -> Optional[Dict[str, Any]]:
    """Settings for the stack's own CalDAV server (omi-host's Radicale), when configured."""
    url = os.getenv('CALDAV_DEFAULT_URL', '').strip()
    username = os.getenv('CALDAV_DEFAULT_USERNAME', '').strip()
    password = os.getenv('CALDAV_DEFAULT_PASSWORD', '')
    if not (url and username and password):
        return None
    return {**settings_db.DEFAULTS, 'url': url, 'username': username, 'password': password, 'bundled': True}


def effective_settings(uid: str) -> Optional[Dict[str, Any]]:
    """The user's stored settings, or the bundled server's until they set their own."""
    stored = settings_db.get_settings(uid)
    if stored and stored.get('url'):
        return {**stored, 'password': settings_db.get_password(uid, stored)}
    return default_settings()


# ---- state kept between ticks -------------------------------------------------------------


def _digest_key(uid: str) -> str:
    return f'caldav:pushed:{uid}'


def _remote_key(uid: str) -> str:
    return f'caldav:remote:{uid}'


def _imported_key(uid: str) -> str:
    return f'caldav:imported:{uid}'


def _hget(key: str, field_name: str) -> str:
    try:
        value = redis_db.r.hget(key, field_name)
    except Exception:  # noqa: BLE001
        return ''
    if value is None:
        return ''
    return value.decode() if isinstance(value, bytes) else str(value)


def _hset(key: str, field_name: str, value: str) -> None:
    try:
        redis_db.r.hset(key, field_name, value)
    except Exception:  # noqa: BLE001 — losing the memo only costs a redundant PUT next tick
        pass


def _hdel(key: str, field_name: str) -> None:
    try:
        redis_db.r.hdel(key, field_name)
    except Exception:  # noqa: BLE001
        pass


def _digest(text: str) -> str:
    # DTSTAMP changes every render; hash the rest so an unchanged item is not re-PUT.
    stable = '\n'.join(l for l in text.splitlines() if not l.startswith(('DTSTAMP:', 'LAST-MODIFIED:')))
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()


# ---- calendars ----------------------------------------------------------------------------


def ensure_calendars(client: CalDavClient, settings: Dict[str, Any]) -> Dict[str, str]:
    """Resolve (creating on the bundled server) the event and task calendars to use."""
    calendar_href = str(settings.get('calendar_href') or '')
    tasks_href = str(settings.get('tasks_href') or '')
    if calendar_href and tasks_href:
        return {'calendar_href': calendar_href, 'tasks_href': tasks_href}
    home = client.calendar_home()
    calendars = client.list_calendars(home)
    by_name = {c.name.strip().lower(): c for c in calendars if c.name}
    if not calendar_href:
        found = by_name.get(DEFAULT_CALENDAR_NAME.lower()) or next((c for c in calendars if c.supports('VEVENT')), None)
        if found is None:
            href = home + ('' if home.endswith('/') else '/') + 'omi/'
            client.make_calendar(href, DEFAULT_CALENDAR_NAME, ['VEVENT'])
            found = Calendar(href=href, name=DEFAULT_CALENDAR_NAME, components=['VEVENT'])
        calendar_href = found.href
    if not tasks_href:
        found = by_name.get(DEFAULT_TASKS_NAME.lower()) or next(
            (c for c in calendars if c.supports('VTODO') and c.href != calendar_href), None
        )
        if found is None:
            href = home + ('' if home.endswith('/') else '/') + 'omi-tasks/'
            client.make_calendar(href, DEFAULT_TASKS_NAME, ['VTODO'])
            found = Calendar(href=href, name=DEFAULT_TASKS_NAME, components=['VTODO'])
        tasks_href = found.href
    return {'calendar_href': calendar_href, 'tasks_href': tasks_href}


# ---- tasks --------------------------------------------------------------------------------


def _task_uid(task_id: str) -> str:
    return f'{ical.TASK_UID_PREFIX}{task_id}{ical.UID_DOMAIN}'


def _task_id_from_uid(uid_value: str) -> Optional[str]:
    if uid_value.startswith(ical.TASK_UID_PREFIX):
        return uid_value[len(ical.TASK_UID_PREFIX) :].split('@', 1)[0]
    return None


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def render_task(item: Dict[str, Any], *, now: datetime) -> str:
    conversation_id = item.get('conversation_id')
    description = ''
    if conversation_id:
        description = f'From Omi conversation {conversation_id}'
    return ical.render_todo(
        uid=_task_uid(str(item['id'])),
        summary=str(item.get('description') or '').strip() or 'Untitled task',
        description=description,
        due=_as_datetime(item.get('due_at')),
        completed=bool(item.get('completed')),
        completed_at=_as_datetime(item.get('completed_at')),
        created=_as_datetime(item.get('created_at')),
        last_modified=_as_datetime(item.get('updated_at')) or now,
        categories=['omi'],
        stamp=now,
    )


def push_tasks(uid: str, client: CalDavClient, tasks_href: str, report: SyncReport, *, now: datetime) -> None:
    since = now - timedelta(days=TASK_WINDOW_DAYS)
    items = action_items_db.get_action_items(uid, limit=MAX_TASKS)
    for item in items:
        if item.get('completed'):
            completed_at = _as_datetime(item.get('completed_at')) or _as_datetime(item.get('updated_at'))
            if completed_at and completed_at < since:
                continue
        text = render_task(item, now=now)
        digest = _digest(text)
        task_id = str(item['id'])
        if _hget(_digest_key(uid), task_id) == digest:
            continue
        try:
            client.put(object_href(tasks_href, _task_uid(task_id)), text)
        except CalDavError as error:
            report.errors.append(f'task {task_id}: {error}')
            continue
        _hset(_digest_key(uid), task_id, digest)
        # The server now carries this version: the pull must not read it back as a remote change.
        _hset(_remote_key(uid), task_id, ical.utc_stamp(_as_datetime(item.get('updated_at')) or now))
        report.tasks_pushed += 1


def pull_tasks(uid: str, client: CalDavClient, tasks_href: str, report: SyncReport, *, now: datetime) -> None:
    try:
        objects = client.query(tasks_href, 'VTODO')
    except CalDavError as error:
        report.errors.append(f'tasks pull: {error}')
        return
    for obj in objects:
        for component in ical.parse_components(obj.data):
            if component.kind != 'VTODO' or not component.uid:
                continue
            stamp = ical.utc_stamp(component.last_modified or now)
            task_id = _task_id_from_uid(component.uid)
            if task_id is None:
                task_id = _hget(_imported_key(uid), component.uid) or None
                if task_id is None:
                    task_id = action_items_db.create_action_item(
                        uid,
                        {
                            'description': component.summary or 'Untitled task',
                            'completed': component.is_completed,
                            'due_at': component.due,
                            'source': 'caldav',
                        },
                        idempotency_key=f'caldav:{component.uid}',
                    )
                    _hset(_imported_key(uid), component.uid, task_id)
                    _hset(_remote_key(uid), task_id, stamp)
                    report.tasks_imported += 1
                    continue
            if _hget(_remote_key(uid), task_id) == stamp:
                continue  # this remote version was already applied
            _hset(_remote_key(uid), task_id, stamp)
            # The server now differs from what we last pushed: forget the push
            # memo so Omi's next change to this task is pushed whatever it is.
            _hdel(_digest_key(uid), task_id)
            item = action_items_db.get_action_item(uid, task_id)
            if not item:
                continue
            if bool(item.get('completed')) != component.is_completed:
                action_items_db.mark_action_item_completed(uid, task_id, component.is_completed)
                report.tasks_pulled += 1


# ---- events -------------------------------------------------------------------------------


def _event_uid(kind: str, key: str) -> str:
    return f'{kind}{key}{ical.UID_DOMAIN}'


def _put_if_changed(uid: str, client: CalDavClient, href: str, key: str, text: str, report: SyncReport) -> None:
    digest = _digest(text)
    if _hget(_digest_key(uid), key) == digest:
        return
    try:
        client.put(href, text)
    except CalDavError as error:
        report.errors.append(f'{key}: {error}')
        return
    _hset(_digest_key(uid), key, digest)
    report.events_pushed += 1


def push_day_summaries(
    uid: str, client: CalDavClient, calendar_href: str, report: SyncReport, *, now: datetime
) -> None:
    from utils.day_review import compose_final_text

    for review in day_reviews_db.list_day_reviews(uid, limit=EVENT_PUSH_WINDOW_DAYS):
        date_str = str(review.get('date') or '')
        try:
            day = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        text = compose_final_text(review.get('summary_text') or '', list(review.get('corrections') or []))
        if not text:
            continue
        uid_value = _event_uid(ical.DAY_UID_PREFIX, date_str)
        ics = ical.render_event(
            uid=uid_value,
            summary=f'Omi: {review.get("title") or "Your day"}',
            start=day,
            all_day=True,
            description=text,
            categories=['omi', *sorted({t for s in (review.get('sections') or []) for t in (s.get('tags') or [])})],
            stamp=now,
        )
        _put_if_changed(uid, client, object_href(calendar_href, uid_value), f'day:{date_str}', ics, report)


def _recent_conversations(uid: str, now: datetime) -> List[Dict[str, Any]]:
    """The conversations of the last bundled days: kept ones, rolled-up ones and the
    ones favorited back out. Read by id, so the sync adds no serving query of its own;
    a day's conversations reach the calendar once the day is bundled."""
    ids: List[str] = []
    for review in day_reviews_db.list_day_reviews(uid, limit=EVENT_PUSH_WINDOW_DAYS):
        for cid in [*(review.get('kept_conversation_ids') or []), *(review.get('rolled_up_conversation_ids') or [])]:
            if cid not in ids:
                ids.append(cid)
        for section in review.get('sections') or []:
            for cid in section.get('conversation_ids') or []:
                if cid not in ids:
                    ids.append(cid)
    if not ids:
        return []
    return conversations_db.get_conversations_by_id_without_photos(uid, ids[:MAX_TASKS], include_discarded=True)


def push_conversation_events(
    uid: str,
    client: CalDavClient,
    calendar_href: str,
    report: SyncReport,
    *,
    now: datetime,
    include_conversations: bool,
) -> None:
    for row in _recent_conversations(uid, now):
        if row.get('deleted') or str(row.get('status') or 'completed') != 'completed':
            continue
        if row.get('discarded') and not row.get('day_review_date'):
            continue  # noise the rules dropped; a bundled clip is discarded too but still part of the day
        conversation_id = str(row.get('id') or '')
        structured = row.get('structured') or {}
        title = str(structured.get('title') or '').strip() or 'Conversation'
        for index, event in enumerate(structured.get('events') or []):
            start = _as_datetime(event.get('start')) if isinstance(event, dict) else None
            if not start or not isinstance(event, dict):
                continue
            key = f'{conversation_id}-{index}'
            uid_value = _event_uid(ical.CONVERSATION_EVENT_UID_PREFIX, key)
            ics = ical.render_event(
                uid=uid_value,
                summary=str(event.get('title') or title),
                start=start,
                duration_minutes=int(event.get('duration') or 30),
                description=str(event.get('description') or '') + f'\n\nFrom Omi conversation: {title}',
                categories=['omi', *(row.get('tags') or [])],
                stamp=now,
            )
            _put_if_changed(uid, client, object_href(calendar_href, uid_value), f'event:{key}', ics, report)
        if include_conversations:
            start = _as_datetime(row.get('started_at')) or _as_datetime(row.get('created_at'))
            finish = _as_datetime(row.get('finished_at'))
            if not start:
                continue
            uid_value = _event_uid('omi-conversation-', conversation_id)
            ics = ical.render_event(
                uid=uid_value,
                summary=f'{structured.get("emoji") or ""} {title}'.strip(),
                start=start,
                end=finish if finish and finish > start else None,
                duration_minutes=5,
                description=str(structured.get('overview') or ''),
                categories=['omi', *(row.get('tags') or [])],
                stamp=now,
            )
            _put_if_changed(
                uid, client, object_href(calendar_href, uid_value), f'conversation:{conversation_id}', ics, report
            )


def pull_events(uid: str, client: CalDavClient, calendar_href: str, report: SyncReport, *, now: datetime) -> None:
    start = now - timedelta(days=EVENT_PULL_PAST_DAYS)
    end = now + timedelta(days=EVENT_PULL_FUTURE_DAYS)
    try:
        objects = client.query(calendar_href, 'VEVENT', start=ical.utc_stamp(start), end=ical.utc_stamp(end))
    except CalDavError as error:
        report.errors.append(f'events pull: {error}')
        return
    events: List[Dict[str, Any]] = []
    for obj in objects:
        for component in ical.parse_components(obj.data):
            if component.kind != 'VEVENT' or component.start is None:
                continue
            if component.uid.startswith(('omi-', ical.CONVERSATION_EVENT_UID_PREFIX)):
                continue  # our own pushes are not "the user's calendar"
            finish = component.end or component.start + timedelta(hours=1)
            events.append(
                {
                    'event_id': component.uid,
                    'title': component.summary,
                    'start': component.start.isoformat(),
                    'end': finish.isoformat(),
                    'description': component.description,
                    'location': component.location,
                    'calendar_id': 0,
                    'all_day': component.all_day,
                    'calendar_name': 'CalDAV',
                    'account_name': '',
                    'account_type': 'caldav',
                    'start_ms': int(component.start.timestamp() * 1000),
                    'end_ms': int(finish.timestamp() * 1000),
                }
            )
    device_calendar_db.store_events(uid, events, source='caldav')
    report.events_pulled = len(events)


# ---- the whole sync -----------------------------------------------------------------------


def sync_user(
    uid: str,
    settings: Dict[str, Any],
    *,
    client_factory: Callable[..., CalDavClient] = CalDavClient,
    now: Optional[datetime] = None,
) -> SyncReport:
    now = now or datetime.now(timezone.utc)
    report = SyncReport()
    if not settings.get('enabled', True):
        return report
    with client_factory(
        str(settings['url']), str(settings.get('username') or ''), str(settings.get('password') or '')
    ) as client:
        hrefs = ensure_calendars(client, settings)
        if settings.get('push_tasks'):
            push_tasks(uid, client, hrefs['tasks_href'], report, now=now)
        if settings.get('pull_tasks'):
            pull_tasks(uid, client, hrefs['tasks_href'], report, now=now)
        if settings.get('push_day_summaries'):
            push_day_summaries(uid, client, hrefs['calendar_href'], report, now=now)
        if settings.get('push_conversation_events') or settings.get('push_conversations'):
            push_conversation_events(
                uid,
                client,
                hrefs['calendar_href'],
                report,
                now=now,
                include_conversations=bool(settings.get('push_conversations')),
            )
        if settings.get('pull_events'):
            pull_events(uid, client, hrefs['calendar_href'], report, now=now)
    return report


def run_for_user(uid: str) -> Optional[Dict[str, Any]]:
    """Sync one user with their effective settings; records the outcome on the settings document."""
    settings = effective_settings(uid)
    if not settings:
        return None
    try:
        report = sync_user(uid, settings).as_dict()
    except CalDavError as error:
        settings_db.record_sync(uid, error=str(error))
        return {'error': str(error)}
    except Exception as error:  # noqa: BLE001 — the scheduler must keep going; the user sees last_error
        logger.exception('caldav: sync failed uid=%s', uid)
        settings_db.record_sync(uid, error=f'{type(error).__name__}: {error}')
        return {'error': f'{type(error).__name__}'}
    settings_db.record_sync(uid, error='; '.join(report['errors'][:5]) or None, report=report)
    return report


def test_connection(
    settings: Dict[str, Any], *, client_factory: Callable[..., CalDavClient] = CalDavClient
) -> List[Dict[str, Any]]:
    """Discover the calendars behind the settings (raises CalDavError when the server refuses)."""
    with client_factory(
        str(settings['url']), str(settings.get('username') or ''), str(settings.get('password') or '')
    ) as client:
        return [{'href': c.href, 'name': c.name, 'components': list(c.components)} for c in client.list_calendars()]


def run_once(users: Callable[[], List[str]]) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for uid in users():
        outcome = run_for_user(uid)
        if outcome is not None:
            results[uid] = outcome
    return results


async def tick() -> None:
    import database.day_bundle_users as active_users_db
    from utils.executors import db_executor, run_blocking

    await run_blocking(db_executor, run_once, active_users_db.active_users)
