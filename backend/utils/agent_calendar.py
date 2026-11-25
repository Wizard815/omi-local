"""Calendar events an agent asks for (omi-local): they wait for the user's OK.

An agent connected over MCP (Hermes, say) can read the user's calendar and propose an
event. The proposal never goes straight to CalDAV: it is stored as a heard event of a
small "agent request" conversation, so it shows up in the Calendar check ("Waiting for
your OK") next to the events Omi heard, and goes through the same approve / decline and
the same CalDAV push (utils/family_calendar.py, utils/caldav/sync.py). The request
conversation is marked ``agent_request`` and stays out of the day review.

Reading: the events on the user's CalDAV calendar (the sync's pulled snapshot, plus a
phone's device-calendar snapshot when one syncs), and Omi's own events, approved or
waiting, each with its state.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import database.device_calendar as device_calendar_db
from models.conversation import Conversation
from utils import family_calendar
from utils.conversations import lifecycle

AGENT_REQUEST_FIELD = 'agent_request'
MAX_TITLE = 200
MAX_DESCRIPTION = 2000
MIN_DURATION = 5
MAX_DURATION = 24 * 60
MAX_AHEAD = timedelta(days=366)


class AgentCalendarError(ValueError):
    """The request cannot become an event (bad title, start or length)."""


def is_agent_request(row: Optional[Dict[str, Any]]) -> bool:
    return bool((row or {}).get(AGENT_REQUEST_FIELD))


def _clean_text(value: Any, field: str, limit: int, *, required: bool) -> str:
    text = str(value or '').strip()
    if required and not text:
        raise AgentCalendarError(f'{field} is required')
    if len(text) > limit:
        raise AgentCalendarError(f'{field} is too long (max {limit} characters)')
    return text


def _parse_start(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or '').strip()
        if not text:
            raise AgentCalendarError('start is required (ISO 8601 with an offset)')
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError as error:
            raise AgentCalendarError(
                f'Invalid start: {text!r}. Use ISO 8601, e.g. 2026-11-26T16:00:00-05:00'
            ) from error
    if parsed.tzinfo is None:
        raise AgentCalendarError('start needs a UTC offset or Z, so the time is not guessed')
    return parsed.astimezone(timezone.utc)


def propose_event(
    uid: str,
    *,
    title: Any,
    start: Any,
    duration: Any = 30,
    description: Any = '',
    agent: Any = 'agent',
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Store an agent's event as waiting for the user's OK; returns how the Calendar check lists it."""
    now = now or datetime.now(timezone.utc)
    clean_title = _clean_text(title, 'title', MAX_TITLE, required=True)
    clean_description = _clean_text(description, 'description', MAX_DESCRIPTION, required=False)
    who = _clean_text(agent, 'agent', 60, required=False) or 'agent'
    begins = _parse_start(start)
    if begins < now - family_calendar.PAST_GRACE:
        raise AgentCalendarError('start is in the past')
    if begins > now + MAX_AHEAD:
        raise AgentCalendarError('start is more than a year ahead')
    try:
        minutes = int(duration if duration is not None else 30)
    except (TypeError, ValueError) as error:
        raise AgentCalendarError('duration must be a whole number of minutes') from error
    if not MIN_DURATION <= minutes <= MAX_DURATION:
        raise AgentCalendarError(f'duration must be {MIN_DURATION}-{MAX_DURATION} minutes')

    conversation_id = str(uuid.uuid4())
    event = {
        'title': clean_title,
        'description': clean_description,
        'start': begins,
        'duration': minutes,
        'created': False,
    }
    conversation = Conversation(
        id=conversation_id,
        created_at=now,
        started_at=now,
        finished_at=now,
        source='external_integration',
        structured={
            'title': f'{who} asked to add: {clean_title}',
            'overview': clean_description or f'{who} proposed this event; it waits for your OK.',
            'emoji': '📅',
            'category': 'other',
            'events': [event],
        },
        transcript_segments=[],
        status='completed',
        language='en',
    )
    data = conversation.model_dump()
    data[AGENT_REQUEST_FIELD] = {'agent': who, 'kind': 'calendar_event', 'requested_at': now}
    lifecycle.create_completed_conversation(uid, data)
    return {
        'conversation_id': conversation_id,
        'event_idx': 0,
        'title': clean_title,
        'description': clean_description,
        'start': begins.isoformat(),
        'duration': minutes,
        'status': 'waiting_for_approval',
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def list_events(uid: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """The user's calendar between start and end: what is on their CalDAV calendar, and Omi's
    events with their state (``on_calendar``, ``waiting_for_approval``, ``declined``)."""
    out: List[Dict[str, Any]] = []
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    for event in device_calendar_db.get_events(uid, start_ms, end_ms):
        out.append(
            {
                'source': 'calendar',
                'status': 'on_calendar',
                'title': event.get('title') or '',
                'start': event.get('start'),
                'end': event.get('end'),
                'all_day': bool(event.get('all_day')),
                'location': event.get('location') or '',
                'description': event.get('description') or '',
                'calendar': event.get('calendar_name') or event.get('account_type') or '',
            }
        )
    waiting, recent = family_calendar.pending(uid)
    for heard in [*waiting, *recent]:
        if not start <= heard.start <= end:
            continue
        if heard.declined:
            status = 'declined'
        elif heard.approved:
            status = 'on_calendar'
        else:
            status = 'waiting_for_approval'
        out.append(
            {
                'source': 'omi',
                'status': status,
                'title': heard.title,
                'start': _iso(heard.start),
                'end': _iso(heard.start + timedelta(minutes=heard.duration or 30)),
                'all_day': False,
                'description': heard.description,
                'conversation_id': heard.conversation_id,
                'event_idx': heard.event_idx,
                'heard_in': heard.conversation_title,
            }
        )
    out.sort(key=lambda e: str(e.get('start') or ''))
    return out
