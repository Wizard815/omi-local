"""One user's CalDAV sync (routers/caldav.py runs it on demand; the local scheduler
runs it for every active member every CALDAV_SYNC_SECONDS and, within ~20 s, for
members whose tasks or day review just changed).

Tasks (VTODO, ``tasks_href``)
  push: every active action item and the ones completed in the last 30 days
        become ``omi-task-<id>`` VTODOs. An item is PUT only when its rendered
        text changed since the last push (Redis hash of digests), so an hourly
        tick costs nothing when nothing moved. A task deleted in Omi has its
        VTODO deleted. A task imported from the server keeps its own UID and
        href (pushing it as ``omi-task-<id>`` would show it twice there).
  pull: a VTODO changed elsewhere (phone, desktop client) is applied to the Omi
        task once per remote change (the remote LAST-MODIFIED is remembered):
        completion, title and due date. A VTODO deleted there deletes the Omi
        task. VTODOs created outside Omi are imported once as action items
        (source "caldav") and then kept in step the same way.
  conflicts: the push runs first, so when both sides changed a task between
        two syncs Omi's version wins and the phone's edit is overwritten.

Calendar (VEVENT, ``calendar_href``)
  push: day summaries as all-day events (``omi-day-<date>``), the events the
        pipeline extracted from conversations (``omi-event-<conversation>-<n>``)
        and, when asked, the conversations themselves as time blocks. A declined
        extracted event, or one whose conversation was deleted, is taken off.
  pull: the calendar's events for the last week and the next 30 days land in
        the device-calendar snapshot (source "caldav"), where the chat's
        calendar tool and the app already look. An extracted event edited on
        the phone (title, start, length) is written back to its conversation;
        one deleted there is declined. Day summaries and conversation blocks
        are Omi's own: edits to them on the phone are overwritten.

The memos (Redis) describe one tasks list and one calendar; pointing the sync at
other collections starts them over rather than reading everything as deleted.

Who owns what on a shared account (omi-host's Radicale has one CalDAV login for
every Omi user on the server):
  collections: each Omi user gets a calendar and a task list of their own
        ("Omi – <name>", "Omi Tasks – <name>"), created on the first sync, or
        picks others in the app. A user who already synced to the old shared
        "Omi"/"Omi Tasks" keeps those (their binding memo says so): nothing is
        moved or re-created on anyone's phone.
  UIDs: everything Omi writes carries the owner's tag (``u`` + 8 hex of a hash
        of the Omi uid): ``omi-day-<tag>-<date>``, ``omi-task-<tag>-<id>``,
        ``omi-event-<tag>-<conversation>-<n>``. Two users who pick the same
        collection never overwrite each other, and the pull leaves the other
        user's objects alone. A migrated user keeps the old unscoped task and
        event UIDs (task and conversation ids are unique, so they never
        collided) until they move to another collection; their old
        ``omi-day-<date>`` summaries, which did collide, are replaced by
        scoped ones on the next sync.
  parent mode (``task_mode = 'parent'``): instead of a list of their own, the
        user's tasks are subtasks (RELATED-TO;RELTYPE=PARENT) of one
        "<name>'s tasks" VTODO in a shared list. Only that parent's children
        are theirs on the pull, so a subtask made on the phone is imported.

Approval: nothing reaches the server before the user said yes. Tasks waiting for
approval (``approval = 'pending'``) or declined are left out (and taken off if
they were there); extracted events go only once approved (or, for ones pushed
before approvals existed, kept as approved); day summaries only once the day is
approved (a draft never), and come off again when the day is reopened.

Nothing here raises past ``sync_user``: it returns a report, and the caller
stores the report or the error on the settings document.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import database.action_items as action_items_db
import database.caldav_settings as settings_db
import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.device_calendar as device_calendar_db
import database.redis_db as redis_db
from utils.caldav import ical
from utils.caldav.client import CalDavClient, CalDavError, Calendar, object_href
from utils.caldav.owners import (
    DEFAULT_CALENDAR_NAME,
    DEFAULT_TASKS_NAME,
    Scope,
    others_on,
    owner_label,
    owner_tag,
    parent_summary,
    parse_owned,
    personal_names,
    register_collections,
    scope_for,
)

logger = logging.getLogger(__name__)

TASK_WINDOW_DAYS = 30
EVENT_PUSH_WINDOW_DAYS = 14
EVENT_PULL_PAST_DAYS = 7
EVENT_PULL_FUTURE_DAYS = 30
MAX_TASKS = 500
# DEFAULT_CALENDAR_NAME / DEFAULT_TASKS_NAME (utils/caldav/owners.py): the collections every user
# shared before per-person ones, kept for the users already on them.
MODES = ('list', 'parent')


@dataclass
class SyncReport:
    tasks_pushed: int = 0
    tasks_pulled: int = 0
    tasks_imported: int = 0
    tasks_removed: int = 0  # VTODOs taken off the server because the task was deleted in Omi
    tasks_deleted: int = 0  # Omi tasks deleted because their VTODO was deleted on the server
    events_pushed: int = 0
    events_pulled: int = 0
    events_removed: int = 0  # VEVENTs taken off the server (declined, or their conversation deleted)
    events_updated: int = 0  # extracted events changed in Omi from an edit made on the server
    events_declined: int = 0  # extracted events declined in Omi because they were deleted on the server
    errors: List[str] = field(default_factory=list)
    # What the sync left out on purpose (a tasks-only server has nowhere for events).
    notes: List[str] = field(default_factory=list)
    # Tasks whose push failed this pass: the pull must not take the server's older copy for
    # a change made there. Not part of the report.
    unpushed: Set[str] = field(default_factory=set, repr=False)
    # The collections this pass resolved (persisted by run_for_user). Not part of the report.
    collections: Dict[str, str] = field(default_factory=dict, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        return {
            'tasks_pushed': self.tasks_pushed,
            'tasks_pulled': self.tasks_pulled,
            'tasks_imported': self.tasks_imported,
            'tasks_removed': self.tasks_removed,
            'tasks_deleted': self.tasks_deleted,
            'events_pushed': self.events_pushed,
            'events_pulled': self.events_pulled,
            'events_removed': self.events_removed,
            'events_updated': self.events_updated,
            'events_declined': self.events_declined,
            'errors': list(self.errors),
            'notes': list(self.notes),
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
    """The user's stored settings, or the bundled server's until they set their own. On the
    bundled server the user's own choices (collections, mode, what to sync) still apply."""
    stored = settings_db.get_settings(uid)
    if stored and stored.get('url'):
        return {**stored, 'password': settings_db.get_password(uid, stored)}
    default = default_settings()
    if default is None:
        return None
    if stored:
        default.update({k: stored[k] for k in settings_db.USER_CHOICES if k in stored})
    return default


# ---- change-triggered sync ------------------------------------------------------------------

DIRTY_KEY = 'caldav:dirty'


def mark_dirty(uid: str) -> None:
    """A task or day summary changed: sync this user at the next short pass (seconds, not the
    hour). Called from the action-item store and the day bundle; never raises."""
    try:
        redis_db.r.sadd(DIRTY_KEY, uid)
    except Exception:  # noqa: BLE001 — the hourly full sync still covers it
        pass


def take_dirty() -> List[str]:
    try:
        raw = redis_db.r.spop(DIRTY_KEY, 200) or []
    except Exception:  # noqa: BLE001
        return []
    return [v.decode() if isinstance(v, bytes) else str(v) for v in raw]


def run_dirty() -> Dict[str, Any]:
    """Sync every user marked dirty since the last pass (users without a CalDAV server are skipped)."""
    results: Dict[str, Any] = {}
    for uid in take_dirty():
        outcome = run_for_user(uid)
        if outcome is not None and outcome.get('busy'):
            mark_dirty(uid)  # the running sync may have read the state before this change: go again
        if outcome is not None:
            results[uid] = outcome
    return results


async def dirty_tick() -> None:
    from utils.executors import db_executor, run_blocking

    await run_blocking(db_executor, run_dirty)


# ---- state kept between ticks -------------------------------------------------------------


def _digest_key(uid: str) -> str:
    return f'caldav:pushed:{uid}'


def _remote_key(uid: str) -> str:
    return f'caldav:remote:{uid}'


def _imported_key(uid: str) -> str:
    return f'caldav:imported:{uid}'


def _href_key(uid: str) -> str:
    """task id -> the href its VTODO has on the server (a phone-made one keeps the phone's)."""
    return f'caldav:href:{uid}'


def _remote_events_key(uid: str) -> str:
    """'event:<conversation>-<n>' -> '<LAST-MODIFIED Omi wrote> <DTSTART>' of an extracted event Omi pushed."""
    return f'caldav:remote-events:{uid}'


def _binding_key(uid: str) -> str:
    return f'caldav:collections:{uid}'


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


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _hgetall(key: str) -> Dict[str, str]:
    """The whole memo; empty when Redis cannot answer (then nothing reads as deleted)."""
    try:
        raw = redis_db.r.hgetall(key) or {}
    except Exception:  # noqa: BLE001
        return {}
    return {_text(k): _text(v) for k, v in raw.items()}


SCOPED_BINDING = 'owner'


def bound_collections(uid: str) -> Optional[Tuple[str, str, bool]]:
    """(tasks_href, calendar_href, legacy) this user's memos describe, or None before the first
    sync. ``legacy``: bound before UIDs carried the owner (the binding has no scheme part)."""
    try:
        bound = redis_db.r.get(_binding_key(uid))
    except Exception:  # noqa: BLE001
        return None
    if bound is None:
        return None
    parts = _text(bound).split('|')
    if len(parts) < 2:
        return None
    return parts[0], parts[1], len(parts) < 3 or parts[2] != SCOPED_BINDING


def bind_collections(uid: str, tasks_href: str, calendar_href: str) -> bool:
    """The memos describe one tasks list and one calendar. When the settings point the
    sync somewhere else (the app's picker, or a server change), start them over: otherwise
    every task or event missing from the new collection would read as deleted on the
    phone. (The import map stays: it is keyed by the phone's UIDs, and the import is
    idempotent on them anyway.)

    Returns whether this user keeps the old unscoped task/event UIDs: only while they stay
    on the collections they were bound to before owners were written into UIDs. A fresh
    start writes owner-scoped UIDs from the first object on."""
    wanted = f'{tasks_href}|{calendar_href}'
    previous = bound_collections(uid)
    try:
        if previous is not None and (previous[0], previous[1]) == (tasks_href, calendar_href):
            return previous[2]
        if previous is not None:
            redis_db.r.delete(_digest_key(uid), _remote_key(uid), _href_key(uid), _remote_events_key(uid))
        redis_db.r.set(_binding_key(uid), f'{wanted}|{SCOPED_BINDING}')
    except Exception:  # noqa: BLE001 — without Redis the memos are unreadable anyway
        pass
    return False


def _version(etag: Optional[str], last_modified: Optional[datetime], now: datetime) -> str:
    """What marks one version of an object on the server: its ETag, which every write
    changes, or else its LAST-MODIFIED (whole seconds: two saves in one second look alike)."""
    return (etag or '').strip() or ical.utc_stamp(last_modified or now)


def _digest(text: str) -> str:
    # DTSTAMP changes every render; hash the rest so an unchanged item is not re-PUT.
    stable = '\n'.join(l for l in text.splitlines() if not l.startswith(('DTSTAMP:', 'LAST-MODIFIED:')))
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()


# ---- calendars ----------------------------------------------------------------------------


# A server that answers MKCALENDAR with one of these has no room for a second calendar
# (Vikunja behind ALLGlance: task lists only, created in the app, 404 on MKCALENDAR).
_NO_MKCALENDAR = {403, 404, 405, 501}

TASKS_ONLY_NOTE = (
    'This server only has task lists (no events calendar): tasks sync both ways, '
    'but day summaries and events are not pushed or pulled. Point Omi at a CalDAV '
    'server with calendars for those.'
)


def events_wanted(settings: Dict[str, Any]) -> bool:
    """Whether any direction needs an events (VEVENT) calendar at all."""
    return any(
        bool(settings.get(key))
        for key in ('push_day_summaries', 'push_conversation_events', 'push_conversations', 'pull_events')
    )


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')[:40] or 'list'


def make_collection(client: CalDavClient, name: str, kind: str, *, home: Optional[str] = None) -> Calendar:
    """MKCALENDAR a calendar (``kind`` 'calendar', VEVENT) or task list ('tasks', VTODO) named
    ``name`` under the calendar home, at a free href derived from the name."""
    home = home or client.calendar_home()
    base = home + ('' if home.endswith('/') else '/')
    taken = {c.href.rstrip('/') for c in client.list_calendars(home)}
    slug = _slug(name)
    href = f'{base}{slug}/'
    n = 2
    while href.rstrip('/') in taken:
        href = f'{base}{slug}-{n}/'
        n += 1
    components = ['VEVENT'] if kind == 'calendar' else ['VTODO']
    client.make_calendar(href, name, components)
    return Calendar(href=href, name=name, components=components)


def ensure_calendars(
    client: CalDavClient,
    settings: Dict[str, Any],
    *,
    need_events: bool = True,
    uid: Optional[str] = None,
    label: Optional[str] = None,
) -> Dict[str, str]:
    """Resolve (creating on the server) the task list and, when an event direction is on,
    the events calendar. ``calendar_href`` comes back empty on a tasks-only server (Vikunja,
    ALLGlance): the caller skips the event directions.

    With ``label`` the user gets collections of their own ("Omi – <label>", found by name or
    created); a server that refuses MKCALENDAR falls back to a collection no other Omi user
    syncs into. Without it, the shared "Omi"/"Omi Tasks" of the single-user days."""
    calendar_href = str(settings.get('calendar_href') or '')
    tasks_href = str(settings.get('tasks_href') or '')
    if tasks_href and (calendar_href or not need_events):
        return {'calendar_href': calendar_href, 'tasks_href': tasks_href}
    if label:
        calendar_name, tasks_name = personal_names(label)
    else:
        calendar_name, tasks_name = DEFAULT_CALENDAR_NAME, DEFAULT_TASKS_NAME

    def free(c: Calendar) -> bool:
        return not uid or not others_on(uid, c.href)

    home = client.calendar_home()
    calendars = client.list_calendars(home)
    by_name = {c.name.strip().lower(): c for c in calendars if c.name}
    if need_events and not calendar_href:
        found = by_name.get(calendar_name.lower())
        if found is not None and not free(found):
            found = None  # someone else on the account has the same name: make one of their own
        if found is None and not label:
            found = next((c for c in calendars if c.supports('VEVENT')), None)
        if found is None:
            try:
                found = make_collection(client, calendar_name, 'calendar', home=home)
            except CalDavError as error:
                if error.status not in _NO_MKCALENDAR:
                    raise
                found = next((c for c in calendars if c.supports('VEVENT') and free(c)), None)
        calendar_href = found.href if found is not None else ''
    if not tasks_href:
        found = by_name.get(tasks_name.lower())
        if found is not None and not free(found):
            found = None
        if found is None and not label:
            found = next((c for c in calendars if c.supports('VTODO') and c.href != calendar_href), None)
        if found is None:
            try:
                found = make_collection(client, tasks_name, 'tasks', home=home)
            except CalDavError as error:
                if error.status not in _NO_MKCALENDAR:
                    raise
                found = next(
                    (c for c in calendars if c.supports('VTODO') and c.href != calendar_href and free(c)), None
                )
                if found is None:
                    raise CalDavError(
                        'No task list found and the server does not let Omi create one; '
                        'create a list in the app and pick it as the tasks calendar.',
                        error.status,
                    ) from error
        tasks_href = found.href
    return {'calendar_href': calendar_href, 'tasks_href': tasks_href}


# ---- tasks --------------------------------------------------------------------------------


def _task_uid(task_id: str, scope: Optional[Scope] = None) -> str:
    """The UID of a task Omi pushes: unscoped for a migrated user, owner-scoped otherwise."""
    if scope is None:
        return f'{ical.TASK_UID_PREFIX}{task_id}{ical.UID_DOMAIN}'
    return scope.task_uid(task_id)


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


def approved(item: Optional[Dict[str, Any]]) -> bool:
    """Whether a task may be on the server: anything but waiting for approval or declined
    (tasks from before approvals have no field and count as approved)."""
    return bool(item) and str((item or {}).get('approval') or 'approved') == 'approved'


def render_task(item: Dict[str, Any], *, now: datetime, uid_value: Optional[str] = None, parent_uid: str = '') -> str:
    conversation_id = item.get('conversation_id')
    description = ''
    if conversation_id:
        description = f'From Omi conversation {conversation_id}'
    return ical.render_todo(
        uid=uid_value or _task_uid(str(item['id'])),
        summary=str(item.get('description') or '').strip() or 'Untitled task',
        description=description,
        due=_as_datetime(item.get('due_at')),
        completed=bool(item.get('completed')),
        completed_at=_as_datetime(item.get('completed_at')),
        created=_as_datetime(item.get('created_at')),
        last_modified=_as_datetime(item.get('updated_at')) or now,
        categories=['omi'],
        stamp=now,
        parent_uid=parent_uid,
    )


def _imported_by_task(uid: str) -> Dict[str, str]:
    """task id -> the UID of the phone-made VTODO it was imported from."""
    return {task_id: remote_uid for remote_uid, task_id in _hgetall(_imported_key(uid)).items()}


def _forget_task(uid: str, task_id: str, remote_uid: Optional[str] = None) -> None:
    for key in (_digest_key(uid), _remote_key(uid), _href_key(uid)):
        _hdel(key, task_id)
    if remote_uid:
        _hdel(_imported_key(uid), remote_uid)


def _task_gone(item: Optional[Dict[str, Any]]) -> bool:
    return not item or bool(item.get('deleted'))


def _update_task(uid: str, task_id: str, *, description: Optional[str], due_at: Optional[datetime]) -> None:
    """A title/due change from the server, through the same helper the MCP tools use (search
    vector and the phone's reminder follow)."""
    from utils.mcp_action_items import update_action_item

    update_action_item(uid, task_id, description=description, due_at=due_at)


def _delete_task(uid: str, task_id: str) -> None:
    """A VTODO deleted on the server: the task goes too, with its vector and reminder."""
    from utils.mcp_action_items import delete_action_item

    delete_action_item(uid, task_id)


PARENT_KEY = 'parent:'


def push_parent(uid: str, client: CalDavClient, tasks_href: str, scope: Scope, report: SyncReport, *, now: datetime):
    """Parent mode: the "<name>'s tasks" to-do the user's tasks hang under (re-made if the
    phone deleted it; renamed when the user's name changes)."""
    text = ical.render_todo(
        uid=scope.parent_uid, summary=scope.parent_summary, categories=['omi'], last_modified=now, stamp=now
    )
    digest = _digest(text)
    if _hget(_digest_key(uid), PARENT_KEY) == digest:
        return
    try:
        client.put(object_href(tasks_href, scope.parent_uid), text)
    except CalDavError as error:
        report.errors.append(f'parent task: {error}')
        return
    _hset(_digest_key(uid), PARENT_KEY, digest)


def push_tasks(
    uid: str,
    client: CalDavClient,
    tasks_href: str,
    report: SyncReport,
    *,
    now: datetime,
    scope: Optional[Scope] = None,
) -> None:
    scope = scope or scope_for(uid)
    since = now - timedelta(days=TASK_WINDOW_DAYS)
    items = action_items_db.get_action_items(uid, limit=MAX_TASKS)
    imported = _imported_by_task(uid)
    hrefs = _hgetall(_href_key(uid))
    listed: Set[str] = set()
    for item in items:
        task_id = str(item['id'])
        if not approved(item):
            continue  # waiting for the user's OK, or declined: not on the server (taken off below)
        listed.add(task_id)
        if item.get('completed'):
            completed_at = _as_datetime(item.get('completed_at')) or _as_datetime(item.get('updated_at'))
            if completed_at and completed_at < since:
                continue
        # A task made on the phone keeps its own UID and href: pushing it as omi-task-<id>
        # would show it twice there.
        remote_uid = imported.get(task_id) or _task_uid(task_id, scope)
        href = hrefs.get(task_id) or object_href(tasks_href, remote_uid)
        text = render_task(item, now=now, uid_value=remote_uid, parent_uid=scope.parent_uid)
        digest = _digest(text)
        if _hget(_digest_key(uid), task_id) == digest:
            continue
        try:
            etag = client.put(href, text)
        except CalDavError as error:
            report.errors.append(f'task {task_id}: {error}')
            report.unpushed.add(task_id)
            continue
        _hset(_digest_key(uid), task_id, digest)
        if hrefs.get(task_id) != href:
            _hset(_href_key(uid), task_id, href)
        # The server now carries this version: the pull must not read it back as a remote change.
        _hset(_remote_key(uid), task_id, _version(etag, _as_datetime(item.get('updated_at')), now))
        report.tasks_pushed += 1
    # Deleted in Omi (or no longer approved): every task the server has from us (the remote
    # memo) that is not in the approved listing any more and not approved in the store either.
    # One read per such id; the listing's cap (MAX_TASKS) is why the store is asked rather
    # than trusting its absence from the list.
    for task_id in _hgetall(_remote_key(uid)):
        if task_id in listed:
            continue
        stored = action_items_db.get_action_item(uid, task_id)
        if not _task_gone(stored) and approved(stored):
            continue
        remote_uid = imported.get(task_id)
        try:
            client.delete(hrefs.get(task_id) or object_href(tasks_href, remote_uid or _task_uid(task_id, scope)))
        except CalDavError as error:
            report.errors.append(f'task {task_id} delete: {error}')
            continue
        _forget_task(uid, task_id, remote_uid)
        report.tasks_removed += 1


def _mine(component: ical.Component, scope: Scope) -> Tuple[str, Optional[str]]:
    """Whose VTODO this is: ('mine', task id) for one Omi pushed for this user, ('foreign',
    None) for another Omi user's (or, in parent mode, one under someone else's parent),
    ('phone', None) for one made outside Omi that belongs to this user, ('skip', None) for
    the parent itself."""
    if component.uid.startswith(ical.PARENT_UID_PREFIX):
        return 'skip', None
    whose, task_id = parse_owned(ical.TASK_UID_PREFIX, component.uid, scope.tag)
    if whose == 'foreign':
        return 'foreign', None
    if whose == 'mine':
        return 'mine', task_id
    if scope.parent_uid:
        # Parent mode: a to-do made on the phone is this user's only as a subtask of their parent.
        return ('phone', None) if component.parent_uid == scope.parent_uid else ('foreign', None)
    if scope.shared_tasks:
        return 'foreign', None  # a list shared with another Omi user: whose phone made it is unknowable
    return 'phone', None


def pull_tasks(
    uid: str,
    client: CalDavClient,
    tasks_href: str,
    report: SyncReport,
    *,
    now: datetime,
    scope: Optional[Scope] = None,
) -> None:
    scope = scope or scope_for(uid)
    try:
        objects = client.query(tasks_href, 'VTODO')
    except CalDavError as error:
        report.errors.append(f'tasks pull: {error}')
        return
    hrefs = _hgetall(_href_key(uid))
    present: Set[str] = set()
    for obj in objects:
        for component in ical.parse_components(obj.data):
            if component.kind != 'VTODO' or not component.uid:
                continue
            stamp = _version(obj.etag, component.last_modified, now)
            whose, task_id = _mine(component, scope)
            if whose in ('foreign', 'skip'):
                continue
            if (
                task_id is not None
                and task_id not in hrefs
                and not _hget(_remote_key(uid), task_id)
                and action_items_db.get_action_item(uid, task_id) is None
            ):
                # An unscoped omi-task-<id> this user never pushed and does not have: another
                # user's on a list they share from before owners were in UIDs. Never theirs to
                # remember, so never theirs to delete.
                continue
            if task_id is None:
                task_id = _hget(_imported_key(uid), component.uid) or None
                if task_id is None:
                    # Made on the phone: the user made it, so it counts as approved.
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
                    _hset(_href_key(uid), task_id, obj.href)
                    # Omi's rendering of the new task stands for what the phone has, so the
                    # next push leaves the phone's object (its alarms, its notes) alone
                    # until the task changes in Omi.
                    created = action_items_db.get_action_item(uid, task_id)
                    if created:
                        _hset(
                            _digest_key(uid),
                            task_id,
                            _digest(
                                render_task(created, now=now, uid_value=component.uid, parent_uid=scope.parent_uid)
                            ),
                        )
                    present.add(task_id)
                    report.tasks_imported += 1
                    continue
            present.add(task_id)
            if task_id in report.unpushed:
                continue  # Omi has a newer version the server refused: it is not the phone's edit
            if obj.href and hrefs.get(task_id) != obj.href:
                _hset(_href_key(uid), task_id, obj.href)
            if _hget(_remote_key(uid), task_id) == stamp:
                continue  # this remote version was already applied
            _hset(_remote_key(uid), task_id, stamp)
            item = action_items_db.get_action_item(uid, task_id)
            if item is None or _task_gone(item) or not approved(item):
                _hdel(_digest_key(uid), task_id)
                continue
            applied = True
            changed = False
            if bool(item.get('completed')) != component.is_completed:
                action_items_db.mark_action_item_completed(uid, task_id, component.is_completed)
                changed = True
            # A rename or a new due date (a phone app has no other task fields Omi keeps).
            # Clearing the due date on the phone leaves Omi's: the task helpers cannot clear it.
            summary = (component.summary or '').strip()
            new_description = summary if summary and summary != str(item.get('description') or '').strip() else None
            due = component.due
            new_due = due if due is not None and due != _as_datetime(item.get('due_at')) else None
            if new_description is not None or new_due is not None:
                try:
                    _update_task(uid, task_id, description=new_description, due_at=new_due)
                    changed = True
                except Exception as error:  # noqa: BLE001 — a locked task keeps Omi's text; the rest go on
                    report.errors.append(f'task {task_id} update: {type(error).__name__}')
                    applied = False
            if changed:
                report.tasks_pulled += 1
            current = action_items_db.get_action_item(uid, task_id) if applied else None
            if current and not _task_gone(current):
                # The server's version is now what Omi has: remember Omi's rendering of it, so
                # the next push leaves the phone's object alone until the task changes in Omi
                # (and cannot bring back one the phone deletes in the meantime).
                _hset(
                    _digest_key(uid),
                    task_id,
                    _digest(render_task(current, now=now, uid_value=component.uid, parent_uid=scope.parent_uid)),
                )
            else:
                # Not taken over: Omi's version is pushed back over the server's next time.
                _hdel(_digest_key(uid), task_id)
    _pull_task_deletions(uid, present, report)


def _pull_task_deletions(uid: str, present: Set[str], report: SyncReport) -> None:
    """A VTODO the server had from this sync (pushed or imported) and no longer has was
    deleted there (Tasks.org, DAVx5, Thunderbird): delete the Omi task too.

    Runs after the push, so a task Omi changed since is already back on the server and
    reads as present. An answer with no tasks at all while several are remembered is
    taken for a server problem (an emptied or swapped collection), not a mass delete."""
    known = _hgetall(_remote_key(uid))
    missing = [task_id for task_id in known if task_id not in present]
    if not missing:
        return
    if not present and len(known) > 1:
        report.notes.append(
            f'The task list came back empty while {len(known)} tasks were synced before; '
            'nothing was deleted in Omi. Delete them in Omi if that was intended.'
        )
        return
    imported = _imported_by_task(uid)
    for task_id in missing:
        if not _task_gone(action_items_db.get_action_item(uid, task_id)):
            try:
                _delete_task(uid, task_id)
            except Exception as error:  # noqa: BLE001 — a locked task stays; it is pushed back next time
                report.errors.append(f'task {task_id} delete: {type(error).__name__}')
                _hdel(_digest_key(uid), task_id)
                _hdel(_remote_key(uid), task_id)
                continue
            report.tasks_deleted += 1
        _forget_task(uid, task_id, imported.get(task_id))


# ---- events -------------------------------------------------------------------------------


def _event_uid(kind: str, key: str, scope: Optional[Scope] = None) -> str:
    if scope is None:
        return f'{kind}{key}{ical.UID_DOMAIN}'
    return scope.event_uid(kind, key)


def _put_if_changed(
    uid: str, client: CalDavClient, href: str, key: str, text: str, report: SyncReport
) -> Optional[str]:
    """PUT when the rendering changed; returns the new version's ETag ('' when the server
    sends none), or None when nothing was written."""
    digest = _digest(text)
    if _hget(_digest_key(uid), key) == digest:
        return None
    try:
        etag = client.put(href, text)
    except CalDavError as error:
        report.errors.append(f'{key}: {error}')
        return None
    _hset(_digest_key(uid), key, digest)
    report.events_pushed += 1
    return etag or ''


def _event_key_href(calendar_href: str, key: str, scope: Optional[Scope] = None) -> str:
    """The href of a pushed event from its memo key ('event:<conversation>-<n>' or 'conversation:<id>')."""
    kind, _, rest = key.partition(':')
    prefix = ical.CONVERSATION_EVENT_UID_PREFIX if kind == 'event' else 'omi-conversation-'
    return object_href(calendar_href, _event_uid(prefix, rest, scope))


def _keys_of_conversation(keys: List[str], conversation_id: str) -> List[str]:
    own = f'event:{conversation_id}-'
    return [
        k for k in keys if k == f'conversation:{conversation_id}' or (k.startswith(own) and k[len(own) :].isdigit())
    ]


# Memo keys of day summaries: 'day:<date>' was the unscoped omi-day-<date> (shared by every user
# of an account, so two people's summaries for one date overwrote each other); 'dayu:<date>'
# is the owner-scoped one that replaced it.
LEGACY_DAY_KEY = 'day:'
DAY_KEY = 'dayu:'


def is_approved_review(review: Optional[Dict[str, Any]]) -> bool:
    """A day summary goes to the calendar only once the user approved the day (a draft never)."""
    return bool(review) and str((review or {}).get('status') or 'finalized') == 'finalized'


def push_day_summaries(
    uid: str,
    client: CalDavClient,
    calendar_href: str,
    report: SyncReport,
    *,
    now: datetime,
    scope: Optional[Scope] = None,
) -> None:
    from utils.day_review import compose_final_text

    scope = scope or scope_for(uid)
    memo = _hgetall(_digest_key(uid))
    # The old shared omi-day-<date> objects this user wrote: replaced by scoped ones below. On a
    # shared account the object may hold another user's text by now; they write their own too.
    for key in [k for k in memo if k.startswith(LEGACY_DAY_KEY)]:
        date_str = key[len(LEGACY_DAY_KEY) :]
        try:
            client.delete(object_href(calendar_href, f'{ical.DAY_UID_PREFIX}{date_str}{ical.UID_DOMAIN}'))
        except CalDavError as error:
            report.errors.append(f'{key} delete: {error}')
            continue
        _hdel(_digest_key(uid), key)
    wanted: Set[str] = set()
    for review in day_reviews_db.list_day_reviews(uid, limit=EVENT_PUSH_WINDOW_DAYS):
        date_str = str(review.get('date') or '')
        try:
            day = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not is_approved_review(review):
            continue  # a draft waits for the user's OK
        text = compose_final_text(review.get('summary_text') or '', list(review.get('corrections') or []))
        if not text:
            continue
        wanted.add(DAY_KEY + date_str)
        uid_value = scope.day_uid(date_str)
        ics = ical.render_event(
            uid=uid_value,
            summary=f'Omi: {review.get("title") or "Your day"}',
            start=day,
            all_day=True,
            description=text,
            categories=['omi', *sorted({t for s in (review.get('sections') or []) for t in (s.get('tags') or [])})],
            stamp=now,
        )
        _put_if_changed(uid, client, object_href(calendar_href, uid_value), DAY_KEY + date_str, ics, report)
    # Reopened (back to a draft, or gone): its summary comes off the calendar until approved again.
    for key in [k for k in memo if k.startswith(DAY_KEY) and k not in wanted]:
        date_str = key[len(DAY_KEY) :]
        if is_approved_review(day_reviews_db.get_day_review(uid, date_str)):
            continue  # older than the listing window: still approved, left as it is
        try:
            client.delete(object_href(calendar_href, scope.day_uid(date_str)))
        except CalDavError as error:
            report.errors.append(f'{key} delete: {error}')
            continue
        _hdel(_digest_key(uid), key)
        report.events_removed += 1


def _render_extracted_event(
    row: Dict[str, Any],
    index: int,
    event: Dict[str, Any],
    start: datetime,
    now: datetime,
    scope: Optional[Scope] = None,
) -> str:
    structured = row.get('structured') or {}
    title = str(structured.get('title') or '').strip() or 'Conversation'
    return ical.render_event(
        uid=_event_uid(ical.CONVERSATION_EVENT_UID_PREFIX, f"{row.get('id')}-{index}", scope),
        summary=str(event.get('title') or title),
        start=start,
        duration_minutes=int(event.get('duration') or 30),
        description=str(event.get('description') or '') + f'\n\nFrom Omi conversation: {title}',
        categories=['omi', *(row.get('tags') or [])],
        stamp=now,
    )


def event_approved(event: Dict[str, Any]) -> bool:
    """An extracted event the user said yes to (the Calendar check), or one already put on a
    calendar (dayGLANCE). Declined wins."""
    return not event.get('declined') and bool(event.get('approved') or event.get('created'))


APPROVED_EVENTS_KEY = 'caldav:approved-event-conversations:'
MAX_APPROVED_CONVERSATIONS = 300


def remember_approved_event(uid: str, conversation_id: str) -> None:
    """The Calendar check approved an event of this conversation: the sync reads it by id
    (no serving query), whether or not its day has been reviewed. Never raises."""
    try:
        key = APPROVED_EVENTS_KEY + uid
        redis_db.r.zadd(key, {conversation_id: time.time()})
        redis_db.r.zremrangebyrank(key, 0, -(MAX_APPROVED_CONVERSATIONS + 1))
    except Exception:  # noqa: BLE001 — the day's own review still lists the conversation
        pass


def _approved_event_conversations(uid: str) -> List[str]:
    try:
        raw = redis_db.r.zrevrange(APPROVED_EVENTS_KEY + uid, 0, MAX_APPROVED_CONVERSATIONS - 1) or []
    except Exception:  # noqa: BLE001
        return []
    return [_text(v) for v in raw]


def _recent_conversation_ids(uid: str, pushed_keys: Optional[List[str]] = None) -> List[str]:
    """The conversations whose events the sync looks at, read by id so the sync adds no serving
    query of its own: those with an event approved lately, those of the last reviewed days
    (kept, rolled up, favorited back out), and those it pushed an event of before (to take
    it off when it is declined or the conversation deleted)."""
    ids: List[str] = []

    def add(cid: str) -> None:
        if cid and cid not in ids:
            ids.append(cid)

    for cid in _approved_event_conversations(uid):
        add(cid)
    for review in day_reviews_db.list_day_reviews(uid, limit=EVENT_PUSH_WINDOW_DAYS):
        for cid in [*(review.get('kept_conversation_ids') or []), *(review.get('rolled_up_conversation_ids') or [])]:
            add(cid)
        for section in review.get('sections') or []:
            for cid in section.get('conversation_ids') or []:
                add(cid)
    for key in pushed_keys or []:
        kind, _, rest = key.partition(':')
        if kind == 'conversation':
            add(rest)
        elif kind == 'event':
            parsed = _event_from_key(key)
            if parsed:
                add(parsed[0])
    return ids[:MAX_TASKS]


def push_conversation_events(
    uid: str,
    client: CalDavClient,
    calendar_href: str,
    report: SyncReport,
    *,
    now: datetime,
    include_conversations: bool,
    scope: Optional[Scope] = None,
) -> None:
    """Every approved extracted event; an event pushed before that is now declined, or whose
    conversation was deleted, comes off the calendar again. An event pushed before approvals
    existed is kept, and marked approved so it stays (it is already on the user's phones).
    Conversations outside the window are left as they are on the server."""
    scope = scope or scope_for(uid)
    pushed_keys = [k for k in _hgetall(_digest_key(uid)) if k.startswith(('event:', 'conversation:'))]
    ids = _recent_conversation_ids(uid, pushed_keys)
    if not ids:
        return
    rows = conversations_db.get_conversations_by_id_without_photos(uid, ids, include_discarded=True)
    wanted: Set[str] = set()
    settled: Set[str] = set()  # conversations whose pushed events are judged here (the rest stay untouched)
    found: Set[str] = set()
    for row in rows:
        conversation_id = str(row.get('id') or '')
        found.add(conversation_id)
        if row.get('deleted'):
            settled.add(conversation_id)
            continue
        if str(row.get('status') or 'completed') != 'completed':
            continue  # being reprocessed: keep what the calendar has until it settles
        settled.add(conversation_id)
        if row.get('discarded') and not row.get('day_review_date'):
            continue  # noise the rules dropped; a bundled clip is discarded too but still part of the day
        structured = row.get('structured') or {}
        title = str(structured.get('title') or '').strip() or 'Conversation'
        for index, event in enumerate(structured.get('events') or []):
            if not isinstance(event, dict):
                continue
            key = f'event:{conversation_id}-{index}'
            start = _as_datetime(event.get('start'))
            if event.get('declined'):
                continue  # the user said no (family calendar, or deleted on the phone)
            if not event_approved(event):
                if key not in pushed_keys:
                    continue  # waiting for the user's OK in the Calendar check
                # Pushed before approvals existed: it is on the user's phones already. Keep it,
                # and record the approval so it survives a change of calendar.
                _apply_to_conversation_event(uid, key, _mark_approved)
            if not start:
                wanted.add(key)  # unreadable for now; never a reason to delete
                continue
            wanted.add(key)
            ics = _render_extracted_event(row, index, event, start, now, scope)
            etag = _put_if_changed(uid, client, _event_key_href(calendar_href, key, scope), key, ics, report)
            if etag is not None:
                # What Omi wrote, so a later edit on the phone can be told from Omi's own push.
                _hset(_remote_events_key(uid), key, f'{_version(etag, now, now)} {ical.utc_stamp(start)}')
        if include_conversations:
            wanted.add(f'conversation:{conversation_id}')
            start = _as_datetime(row.get('started_at')) or _as_datetime(row.get('created_at'))
            finish = _as_datetime(row.get('finished_at'))
            if not start:
                continue
            uid_value = _event_uid('omi-conversation-', conversation_id, scope)
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
    # Asked for by id and not returned: the conversation is gone.
    settled.update(cid for cid in ids if cid not in found)
    if not include_conversations:
        wanted.update(k for k in pushed_keys if k.startswith('conversation:'))  # the toggle only stops new ones
    for conversation_id in settled:
        for key in _keys_of_conversation(pushed_keys, conversation_id):
            if key in wanted:
                continue
            try:
                client.delete(_event_key_href(calendar_href, key, scope))
            except CalDavError as error:
                report.errors.append(f'{key} delete: {error}')
                continue
            _hdel(_digest_key(uid), key)
            _hdel(_remote_events_key(uid), key)
            report.events_removed += 1


def _mark_approved(event: Dict[str, Any]) -> bool:
    if event.get('approved'):
        return False
    event['approved'] = True
    return True


def _event_from_key(key: str) -> Optional[Tuple[str, int]]:
    """('<conversation id>', n) from 'event:<conversation id>-<n>'."""
    conversation_id, _, index = key.partition(':')[2].rpartition('-')
    if not conversation_id or not index.isdigit():
        return None
    return conversation_id, int(index)


def _apply_to_conversation_event(
    uid: str, key: str, change: Callable[[Dict[str, Any]], bool]
) -> Optional[Dict[str, Any]]:
    """Change one extracted event the way the family calendar does (structured.events[n]).
    Returns the conversation as changed, or None when there was nothing to change."""
    parsed = _event_from_key(key)
    if parsed is None:
        return None
    conversation_id, index = parsed
    conversation = conversations_db.get_conversation(uid, conversation_id)
    if not conversation or conversation.get('deleted'):
        return None
    events = [
        dict(e) if isinstance(e, dict) else e for e in ((conversation.get('structured') or {}).get('events') or [])
    ]
    if not 0 <= index < len(events) or not isinstance(events[index], dict):
        return None
    if not change(events[index]):
        return None
    conversations_db.update_conversation_events(uid, conversation_id, events)
    return {
        **conversation,
        'id': conversation_id,
        'structured': {**(conversation.get('structured') or {}), 'events': events},
    }


def _pull_omi_event(
    uid: str,
    key: str,
    component: ical.Component,
    etag: str,
    report: SyncReport,
    *,
    now: datetime,
    scope: Optional[Scope] = None,
) -> None:
    """An event Omi pushed, as the server has it now: when someone else saved it since (its
    LAST-MODIFIED is not the one Omi wrote), the title, start and length go back to Omi."""
    remembered = _hget(_remote_events_key(uid), key)
    stamp = _version(etag, component.last_modified, now)
    start = component.start
    if not remembered or start is None:
        return  # pushed before Omi remembered its own writes: nothing to compare with
    if remembered.split(' ', 1)[0] == stamp:
        return
    title = (component.summary or '').strip()
    minutes = int((component.end - start).total_seconds() // 60) if component.end and component.end > start else None

    def change(event: Dict[str, Any]) -> bool:
        before = dict(event)
        if title:
            event['title'] = title
        event['start'] = start
        if minutes:
            event['duration'] = minutes
        return event != before

    changed = _apply_to_conversation_event(uid, key, change)
    _hset(_remote_events_key(uid), key, f'{stamp} {ical.utc_stamp(start)}')
    if changed is None:
        return
    report.events_updated += 1
    # The server's version is now what Omi has: remember Omi's rendering of it, so the next
    # push leaves the phone's object alone until the event changes in Omi.
    index = _event_from_key(key)[1]  # type: ignore[index]
    event = changed['structured']['events'][index]
    _hset(_digest_key(uid), key, _digest(_render_extracted_event(changed, index, event, start, now, scope)))


def _pull_event_deletions(
    uid: str, seen: Set[str], total: int, window: Tuple[datetime, datetime], report: SyncReport
) -> None:
    """An extracted event Omi pushed, inside the window the pull asked for, and missing from
    the answer was deleted on the phone: decline it in Omi so it is not pushed again (the
    family calendar shows it as declined). An empty answer while several are remembered is
    taken for a server problem, as with tasks."""
    remembered = _hgetall(_remote_events_key(uid))
    start, end = window
    missing = []
    for key, value in remembered.items():
        if key in seen:
            continue
        when = ical.parse_stamp(value.split(' ', 1)[-1])
        if when is None or not start < when < end:
            continue  # outside what the pull asked for: absence says nothing
        missing.append(key)
    if not missing:
        return
    if total == 0 and len(remembered) > 1:
        report.notes.append('The events calendar came back empty; no event was declined in Omi.')
        return

    def decline(event: Dict[str, Any]) -> bool:
        if event.get('declined'):
            return False
        event['declined'] = True
        event['approved'] = False
        return True

    for key in missing:
        if _apply_to_conversation_event(uid, key, decline) is not None:
            report.events_declined += 1
        _hdel(_remote_events_key(uid), key)
        _hdel(_digest_key(uid), key)


def pull_events(
    uid: str,
    client: CalDavClient,
    calendar_href: str,
    report: SyncReport,
    *,
    now: datetime,
    scope: Optional[Scope] = None,
) -> None:
    scope = scope or scope_for(uid)
    start = now - timedelta(days=EVENT_PULL_PAST_DAYS)
    end = now + timedelta(days=EVENT_PULL_FUTURE_DAYS)
    try:
        objects = client.query(calendar_href, 'VEVENT', start=ical.utc_stamp(start), end=ical.utc_stamp(end))
    except CalDavError as error:
        report.errors.append(f'events pull: {error}')
        return
    events: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    total = 0
    for obj in objects:
        for component in ical.parse_components(obj.data):
            if component.kind != 'VEVENT' or component.start is None:
                continue
            total += 1
            whose, rest = parse_owned(ical.CONVERSATION_EVENT_UID_PREFIX, component.uid, scope.tag)
            if whose == 'mine' and rest:
                key = 'event:' + rest
                seen.add(key)
                _pull_omi_event(uid, key, component, obj.etag, report, now=now, scope=scope)
                continue
            if component.uid.startswith('omi-'):
                # Day summaries, conversation blocks and other Omi users' events: Omi's own, not
                # "the user's calendar".
                continue
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
    # Margins of a day: an event at the edge of the window may or may not be in the answer.
    _pull_event_deletions(uid, seen, total, (start + timedelta(days=1), end - timedelta(days=1)), report)


# ---- the whole sync -----------------------------------------------------------------------


SHARED_LIST_NOTE = (
    'Another person on this server syncs into the same task list. Your tasks are kept apart, '
    'but tasks made on a phone there are not imported (Omi cannot tell whose they are). Pick a '
    'list of your own, or use the parent task mode.'
)


def resolve_scope(uid: str, settings: Dict[str, Any], hrefs: Dict[str, str], *, legacy: bool) -> Scope:
    tag = owner_tag(uid)
    label = str(settings.get('_label') or '') or owner_label(uid)
    parent = settings.get('task_mode') == 'parent'
    return Scope(
        tag=tag,
        legacy=legacy,
        parent_uid=f'{ical.PARENT_UID_PREFIX}{tag}{ical.UID_DOMAIN}' if parent else '',
        parent_summary=parent_summary(label) if parent else '',
        shared_tasks=bool(others_on(uid, hrefs['tasks_href'])),
    )


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
    settings = dict(settings)
    label = owner_label(uid)
    settings['_label'] = label
    if not settings.get('calendar_href') and not settings.get('tasks_href'):
        # Migration: a user who synced before collections were per person stays where they
        # were (the old shared "Omi"/"Omi Tasks", or whatever they were bound to).
        bound = bound_collections(uid)
        if bound is not None:
            settings['tasks_href'], settings['calendar_href'] = bound[0], bound[1]
    with client_factory(
        str(settings['url']), str(settings.get('username') or ''), str(settings.get('password') or '')
    ) as client:
        want_events = events_wanted(settings)
        hrefs = ensure_calendars(client, settings, need_events=want_events, uid=uid, label=label)
        report.collections = dict(hrefs)
        legacy = bind_collections(uid, hrefs['tasks_href'], hrefs['calendar_href'])
        register_collections(uid, label, [hrefs['tasks_href'], hrefs['calendar_href']])
        scope = resolve_scope(uid, settings, hrefs, legacy=legacy)
        if scope.shared_tasks and not scope.parent_uid and settings.get('pull_tasks'):
            report.notes.append(SHARED_LIST_NOTE)
        if settings.get('push_tasks'):
            if scope.parent_uid:
                push_parent(uid, client, hrefs['tasks_href'], scope, report, now=now)
            push_tasks(uid, client, hrefs['tasks_href'], report, now=now, scope=scope)
        if settings.get('pull_tasks'):
            pull_tasks(uid, client, hrefs['tasks_href'], report, now=now, scope=scope)
        if want_events and not hrefs['calendar_href']:
            report.notes.append(TASKS_ONLY_NOTE)
            return report
        if settings.get('push_day_summaries'):
            push_day_summaries(uid, client, hrefs['calendar_href'], report, now=now, scope=scope)
        if settings.get('push_conversation_events') or settings.get('push_conversations'):
            push_conversation_events(
                uid,
                client,
                hrefs['calendar_href'],
                report,
                now=now,
                include_conversations=bool(settings.get('push_conversations')),
                scope=scope,
            )
        if settings.get('pull_events'):
            pull_events(uid, client, hrefs['calendar_href'], report, now=now, scope=scope)
    return report


# One sync per user at a time: "Sync now", the change pass and the full pass run on their own
# clocks, and two passes interleaving their push and pull can undo each other's work (a pass
# that listed the tasks before the other pulled a phone's change pushes the old state back).
LOCK_SECONDS = 300
BUSY = 'A CalDAV sync for this user is already running'


def _lock_key(uid: str) -> str:
    return f'caldav:lock:{uid}'


def _acquire(uid: str, wait: float) -> Optional[str]:
    """A lease token, '' when Redis cannot answer (sync unlocked rather than not at all),
    or None when another sync still holds it after ``wait`` seconds."""
    token = uuid.uuid4().hex
    deadline = time.monotonic() + wait
    while True:
        try:
            if redis_db.r.set(_lock_key(uid), token, nx=True, ex=LOCK_SECONDS):
                return token
        except Exception:  # noqa: BLE001
            return ''
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def _release(uid: str, token: str) -> None:
    if not token:
        return
    try:
        if _text(redis_db.r.get(_lock_key(uid)) or b'') == token:
            redis_db.r.delete(_lock_key(uid))
    except Exception:  # noqa: BLE001 — the lease expires on its own
        pass


def _persist_collections(uid: str, settings: Dict[str, Any], hrefs: Dict[str, str]) -> None:
    """The collections a sync created or migrated become the user's choice (the app shows
    them; the next sync goes straight there)."""
    if not hrefs.get('tasks_href'):
        return
    if (settings.get('calendar_href') or '', settings.get('tasks_href') or '') == (
        hrefs.get('calendar_href') or '',
        hrefs['tasks_href'],
    ):
        return
    try:
        settings_db.set_collections(uid, calendar_href=hrefs.get('calendar_href') or '', tasks_href=hrefs['tasks_href'])
    except Exception:  # noqa: BLE001 — resolved again next time
        logger.warning('caldav: could not store the collections uid=%s', uid)


def run_for_user(uid: str, *, wait: float = 0.0) -> Optional[Dict[str, Any]]:
    """Sync one user with their effective settings; records the outcome on the settings document.
    ``wait``: how long to wait for a sync already running for this user (the scheduler's passes
    do not wait; "Sync now" does). Returns ``{'error': BUSY, 'busy': True}`` when it gave up."""
    settings = effective_settings(uid)
    if not settings:
        return None
    token = _acquire(uid, wait)
    if token is None:
        return {'error': BUSY, 'busy': True}
    try:
        result = sync_user(uid, settings)
        report = result.as_dict()
        _persist_collections(uid, settings, result.collections)
    except CalDavError as error:
        settings_db.record_sync(uid, error=str(error))
        return {'error': str(error)}
    except Exception as error:  # noqa: BLE001 — the scheduler must keep going; the user sees last_error
        logger.exception('caldav: sync failed uid=%s', uid)
        settings_db.record_sync(uid, error=f'{type(error).__name__}: {error}')
        return {'error': f'{type(error).__name__}'}
    finally:
        _release(uid, token)
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


def _describe(uid: str, c: Calendar, settings: Dict[str, Any]) -> Dict[str, Any]:
    others = others_on(uid, c.href)
    mine = []
    if c.href == settings.get('calendar_href'):
        mine.append('calendar')
    if c.href == settings.get('tasks_href'):
        mine.append('tasks')
    return {
        'href': c.href,
        'name': c.name,
        'components': list(c.components),
        'used_by': sorted(others.values()),
        'mine': mine,
    }


def list_collections(
    uid: str, settings: Dict[str, Any], *, client_factory: Callable[..., CalDavClient] = CalDavClient
) -> List[Dict[str, Any]]:
    """The server's calendars and task lists, each with who else on this Omi server syncs into
    it (known from their syncs) and whether it is this user's calendar or task list."""
    with client_factory(
        str(settings['url']), str(settings.get('username') or ''), str(settings.get('password') or '')
    ) as client:
        return [_describe(uid, c, settings) for c in client.list_calendars()]


def create_collection(
    uid: str,
    settings: Dict[str, Any],
    name: str,
    kind: str,
    *,
    client_factory: Callable[..., CalDavClient] = CalDavClient,
) -> Dict[str, Any]:
    """Make a new calendar or task list on the server (MKCALENDAR); the caller picks it."""
    with client_factory(
        str(settings['url']), str(settings.get('username') or ''), str(settings.get('password') or '')
    ) as client:
        made = make_collection(client, name.strip(), kind)
    return _describe(uid, made, settings)


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
