"""The family calendar (omi-local): events Omi hears go to dayGLANCE through the ALLGlance
gateway, and only after the user has reviewed them.

Why a review step: the extractor gets dates and times wrong often enough ("Friday" from a
conversation on a Sunday, a "3" that was 3 pm, a duration it guessed). So nothing here runs
on its own: ``pending`` lists what was heard, ``approve`` writes one event the way the user
corrected it and remembers where it went, ``decline`` remembers that it is not wanted. The
only writer is ``approve``; there is no scheduler and no hook into conversation processing.

Where it writes: POST {gateway}/api/allglance/tasks with source "dayglance" and a start
("YYYY-MM-DD HH:MM" in the user's time zone), the gateway's REST mirror of
allglance_create_task. The gateway's URL and token are the user's "allglance" task
integration, which utils/allglance.py seeds from ALLGLANCE_URL / ALLGLANCE_TOKEN.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

import database.conversations as conversations_db
import database.notifications as notifications_db
import database.users as users_db
from models.family_calendar import FamilyCalendarEvent
from models.structured import Event

logger = logging.getLogger(__name__)

CALENDAR = 'allglance'
LABEL = 'ALLGlance, dayGLANCE'
LOOKBACK_DAYS = 7  # conversations this old can still have events waiting for review
PAST_GRACE = timedelta(hours=12)  # an event that started this long ago is still worth confirming
RECENT_LIMIT = 20


def integration(uid: str) -> Optional[Dict[str, Any]]:
    """The user's ALLGlance gateway (url + token, and the member Omi acts as), or None when there is none."""
    record = users_db.get_task_integration(uid, CALENDAR) or {}
    base_url = str(record.get('base_url') or '').rstrip('/')
    if not base_url or not record.get('access_token'):
        return None
    owner = record.get('owner_id')
    return {'base_url': base_url, 'access_token': record['access_token'], 'owner_id': str(owner) if owner else None}


def set_owner(uid: str, owner_id: Optional[str]) -> None:
    """Bind the events Omi adds (and the tasks it creates) to one household member; None = the whole family.
    Stored on the allglance task integration, which the hourly seed leaves alone for this field."""
    users_db.set_task_integration(uid, CALENDAR, {'owner_id': owner_id or None})


async def members(target: Dict[str, Any], *, client: Optional[httpx.AsyncClient] = None) -> List[Dict[str, str]]:
    """The household from the gateway's roster (dayGLANCE's users and Vikunja's). Empty when it cannot be read."""
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await http.get(
            f"{target['base_url']}/api/allglance/users", headers={'Authorization': f"Bearer {target['access_token']}"}
        )
        if response.status_code != 200:
            return []
        users = (response.json() or {}).get('users') or []
    except Exception:  # noqa: BLE001 — the gateway may be down; the page just has no picker then
        return []
    finally:
        if client is None:
            await http.aclose()
    return [
        {'id': str(u.get('id')), 'name': str(u.get('name') or u.get('id'))}
        for u in users
        if isinstance(u, dict) and u.get('id') is not None
    ]


def _zone(uid: str):
    try:
        return ZoneInfo(notifications_db.resolve_user_timezone(uid))
    except Exception:  # noqa: BLE001 — an odd stored zone falls back to UTC
        return timezone.utc


def _as_utc(value: Any, zone) -> Optional[datetime]:
    """Stored starts may be aware (UTC) or naive (the extractor wrote the user's local time)."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(timezone.utc)


def _events_of(data: Dict[str, Any]) -> List[Event]:
    raw = ((data.get('structured') or {}).get('events')) or []
    out: List[Event] = []
    for item in raw:
        try:
            out.append(Event(**item) if isinstance(item, dict) else item)
        except Exception:  # noqa: BLE001 — one odd event never hides the rest
            continue
    return out


def _present(conversation: Dict[str, Any], idx: int, event: Event, zone) -> FamilyCalendarEvent:
    return FamilyCalendarEvent(
        conversation_id=conversation['id'],
        event_idx=idx,
        conversation_title=str((conversation.get('structured') or {}).get('title') or ''),
        title=event.title,
        description=event.description or '',
        start=_as_utc(event.start, zone) or datetime.now(timezone.utc),
        duration=event.duration,
        heard_at=_as_utc(conversation.get('started_at') or conversation.get('created_at'), zone),
        created=bool(event.created),
        calendar=event.calendar,
        external_id=event.external_id,
        declined=bool(event.declined),
    )


def _recent_conversations(uid: str) -> List[Dict[str, Any]]:
    # The same query shape as the day review's pending scan (started_at window), so it is
    # served by the same Firestore index.
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)
    return conversations_db.get_conversations(
        uid, limit=300, include_discarded=False, start_date=since, end_date=now, date_field='started_at'
    )


def pending(uid: str, *, now: Optional[datetime] = None) -> Tuple[List[FamilyCalendarEvent], List[FamilyCalendarEvent]]:
    """(waiting for review, reviewed lately): events of the last LOOKBACK_DAYS of conversations.

    Waiting = not created, not declined, and not long past (an event that started more than
    PAST_GRACE ago was missed, so asking about it would only add noise)."""
    zone = _zone(uid)
    now = now or datetime.now(timezone.utc)
    waiting: List[FamilyCalendarEvent] = []
    recent: List[FamilyCalendarEvent] = []
    for conversation in _recent_conversations(uid):
        if conversation.get('deleted') or conversation.get('discarded'):
            continue
        for idx, event in enumerate(_events_of(conversation)):
            shown = _present(conversation, idx, event, zone)
            if shown.created or shown.declined:
                recent.append(shown)
            elif shown.start >= now - PAST_GRACE:
                waiting.append(shown)
    waiting.sort(key=lambda e: e.start)
    recent.sort(key=lambda e: e.start, reverse=True)
    return waiting, recent[:RECENT_LIMIT]


def _load(uid: str, conversation_id: str, event_idx: int) -> Tuple[Dict[str, Any], List[Event]]:
    conversation = conversations_db.get_conversation(uid, conversation_id)
    if not conversation or conversation.get('deleted'):
        raise LookupError('conversation not found')
    events = _events_of(conversation)
    if not 0 <= event_idx < len(events):
        raise LookupError('event not found')
    return conversation, events


def _save(uid: str, conversation_id: str, events: List[Event]) -> None:
    conversations_db.update_conversation_events(uid, conversation_id, [e.model_dump() for e in events])


def decline(uid: str, conversation_id: str, event_idx: int) -> FamilyCalendarEvent:
    conversation, events = _load(uid, conversation_id, event_idx)
    events[event_idx].declined = True
    _save(uid, conversation_id, events)
    return _present(conversation, event_idx, events[event_idx], _zone(uid))


def gateway_start(start_utc: datetime, zone) -> str:
    """What dayGLANCE's create takes: the local wall-clock start, minute precision."""
    return start_utc.astimezone(zone).strftime('%Y-%m-%d %H:%M')


def _prepare(
    uid: str,
    conversation_id: str,
    event_idx: int,
    *,
    title: Optional[str],
    description: Optional[str],
    start: Optional[datetime],
    duration: Optional[int],
) -> Dict[str, Any]:
    """The blocking half before the gateway call: load, apply the corrections, build the body."""
    target = integration(uid)
    if target is None:
        raise RuntimeError('No family calendar: set ALLGLANCE_URL / ALLGLANCE_TOKEN or the ALLGlance task integration')
    conversation, events = _load(uid, conversation_id, event_idx)
    event = events[event_idx]
    zone = _zone(uid)
    if event.created and event.calendar == CALENDAR:
        return {'done': _present(conversation, event_idx, event, zone)}
    if title is not None and title.strip():
        event.title = title.strip()
    if description is not None:
        event.description = description.strip()
    if start is not None:
        event.start = _as_utc(start, zone) or event.start
    if duration is not None:
        event.duration = int(duration)
    start_utc = _as_utc(event.start, zone)
    if start_utc is None:
        raise RuntimeError('The event has no usable start time')
    body: Dict[str, Any] = {
        'title': event.title,
        'source': 'dayglance',
        'start': gateway_start(start_utc, zone),
        'duration_minutes': max(5, int(event.duration or 30)),
    }
    if event.description:
        body['notes'] = event.description
    if target.get('owner_id'):
        body['owner_id'] = target['owner_id']
    return {
        'target': target,
        'conversation': conversation,
        'events': events,
        'event': event,
        'zone': zone,
        'start_utc': start_utc,
        'body': body,
    }


def _record(
    uid: str, conversation_id: str, event_idx: int, prepared: Dict[str, Any], payload: Dict[str, Any]
) -> FamilyCalendarEvent:
    """The blocking half after the gateway said yes: remember where the event went."""
    event: Event = prepared['event']
    task = payload.get('task') or {}
    event.created = True
    event.declined = False
    event.calendar = CALENDAR
    external = task.get('id')
    event.external_id = f"{payload.get('source') or 'dayglance'}:{external}" if external is not None else None
    event.start = prepared['start_utc']
    _save(uid, conversation_id, prepared['events'])
    logger.info(
        'family_calendar: uid=%s conversation=%s event=%s added as %s',
        uid,
        conversation_id,
        event_idx,
        event.external_id,
    )
    return _present(prepared['conversation'], event_idx, event, prepared['zone'])


async def approve(
    uid: str,
    conversation_id: str,
    event_idx: int,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    start: Optional[datetime] = None,
    duration: Optional[int] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> FamilyCalendarEvent:
    """Write one reviewed event to the family calendar, with the user's corrections, and remember it.

    Raises LookupError (no such event), RuntimeError (no family calendar, or the gateway said no).
    The Firestore work runs on the db executor; only the gateway call is awaited here."""
    from utils.executors import db_executor, run_blocking

    prepared = await run_blocking(
        db_executor,
        _prepare,
        uid,
        conversation_id,
        event_idx,
        title=title,
        description=description,
        start=start,
        duration=duration,
    )
    if 'done' in prepared:
        return prepared['done']
    target = prepared['target']
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await http.post(
            f"{target['base_url']}/api/allglance/tasks",
            headers={'Authorization': f"Bearer {target['access_token']}", 'Content-Type': 'application/json'},
            json=prepared['body'],
        )
    finally:
        if client is None:
            await http.aclose()
    if response.status_code not in (200, 201):
        detail = ''
        try:
            detail = str((response.json() or {}).get('error') or '')
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f'The family calendar refused it ({response.status_code}){": " + detail if detail else ""}')
    payload = response.json() or {}
    return await run_blocking(db_executor, _record, uid, conversation_id, event_idx, prepared, payload)
