"""Calendar tools (omi-local): read the user's calendar, and propose an event that waits for
the user's OK in the Calendar check before it reaches CalDAV (utils/agent_calendar.py)."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from utils import agent_calendar
from utils.memory.product_authorization import ProductAuthorizationContext
from utils.mcp_dates import local_date_window
from utils.mcp_memories import parse_mcp_int
from utils.mcp_server.errors import ToolExecutionError

DEFAULT_DAYS = 7
MAX_WINDOW = timedelta(days=62)


def get_calendar_events(
    uid: str,
    arguments: Dict[str, Any],
    auth_context: Optional[ProductAuthorizationContext] = None,
) -> Dict[str, Any]:
    start_dt, end_dt, zone = local_date_window(
        uid, arguments.get('start_date'), arguments.get('end_date'), arguments.get('timezone') or 'profile'
    )
    now = datetime.now(timezone.utc)
    start = start_dt or now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = end_dt or start + timedelta(days=DEFAULT_DAYS)
    if end - start > MAX_WINDOW:
        raise ToolExecutionError('The window is at most 62 days', code=-32602)
    events = agent_calendar.list_events(uid, start, end)
    return {
        'events': events,
        'window': {'start': start.isoformat(), 'end': end.isoformat()},
        'timezone': str(zone),
        'waiting_for_approval': sum(1 for e in events if e.get('status') == 'waiting_for_approval'),
    }


def create_calendar_event(
    uid: str,
    arguments: Dict[str, Any],
    auth_context: Optional[ProductAuthorizationContext] = None,
) -> Dict[str, Any]:
    try:
        duration = parse_mcp_int(arguments.get('duration'), 'duration', default=30, minimum=5, maximum=24 * 60)
        event = agent_calendar.propose_event(
            uid,
            title=arguments.get('title'),
            start=arguments.get('start'),
            duration=duration,
            description=arguments.get('description'),
            agent=arguments.get('requested_by') or 'agent',
        )
    except ValueError as error:
        raise ToolExecutionError(str(error), code=-32602)
    return {
        'success': True,
        'event': event,
        'message': 'Waiting for the user\'s OK in Omi; it goes to their calendar once they approve it.',
    }
