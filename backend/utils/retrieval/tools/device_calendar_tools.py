"""
Agentic chat tools for device calendar (Android CalendarContract / local calendar).
Replaces Google Calendar API dependency for self-hosted setups.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

import database.device_calendar as device_calendar_db
from utils.retrieval.tools.integration_base import resolve_config_uid
import logging

logger = logging.getLogger(__name__)


def _format_event(e: dict) -> str:
    """Format a device calendar event for LLM consumption."""
    parts = []
    parts.append(f"  - {e.get('title', '(no title)')}")
    start = e.get("start", "")
    end = e.get("end", "")
    if start and end:
        parts.append(f"    {start} → {end}")
    if e.get("all_day"):
        parts.append("    (all-day)")
    desc = e.get("description", "")
    if desc:
        parts.append(f"    {desc[:120]}")
    loc = e.get("location", "")
    if loc:
        parts.append(f"    Location: {loc}")
    cal = e.get("calendar_name", e.get("account_name", ""))
    if cal:
        parts.append(f"    Calendar: {cal}")
    return "\n".join(parts)


@tool
def get_device_calendar_events_tool(
    days_ahead: int = 7,
    days_behind: int = 0,
    config: RunnableConfig = None,  # type: ignore[reportAssignmentType]  # langchain injects at runtime; None default for direct calls
) -> str:
    """Get calendar events from the user's device calendar (Android CalendarContract).

    This reads events from WHATEVER calendar the user has on their phone —
    Google, DAVx5/CalDAV (Nextcloud), Exchange, or local device calendars.
    No cloud API dependency. No Google OAuth needed.

    Args:
        days_ahead: How many days into the future to look (default 7).
        days_behind: How many days into the past to look (default 0).

    Returns:
        A formatted list of upcoming and recent calendar events.
    """
    from datetime import timedelta

    # The user comes from the run config like every other tool. This used to be
    # an LLM-visible `uid` argument the docstring called "injected" — nothing
    # injected it, so the model had to invent an id and never found any events.
    uid, uid_err = resolve_config_uid(cast(Optional[Dict[str, Any]], config))
    if uid_err:
        return uid_err
    assert uid is not None

    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(days=days_behind)).timestamp() * 1000)
    end_ms = int((now + timedelta(days=days_ahead)).timestamp() * 1000)

    events = device_calendar_db.get_events(uid, start_ms=start_ms, end_ms=end_ms)

    if not events:
        return (
            "No device calendar events found. "
            "The phone app needs to sync calendar data first. "
            "Open the Omi app and check Settings → Calendar → Sync Device Calendar."
        )

    # Sort by start time
    events.sort(key=lambda e: e.get("start_ms", 0))

    lines = [f"Device calendar events ({len(events)} total, next {days_ahead} days):"]
    for e in events:
        lines.append(_format_event(e))

    return "\n".join(lines)


def get_device_calendar_events_tool_config():
    """Return the tool name and function for registration in agentic chat."""
    return {
        "name": "get_device_calendar_events_tool",
        "description": "Get upcoming calendar events from the user's device calendar (Android CalendarContract). Works with Google Calendar, DAVx5/CalDAV (Nextcloud), Exchange, and local device calendars — all through the phone's built-in calendar provider.",
        "func": get_device_calendar_events_tool,
    }


async def device_calendar_tools_for(uid: str) -> List[Any]:
    """[the device-calendar tool] for a self-hosted user with a synced calendar, else [].

    Local mode counts a primary model picked in the dashboard (is_local_mode), not
    just OMI_LOCAL_MODEL — the env check alone hid the tool for those setups.
    """
    from utils.executors import db_executor, run_blocking
    from utils.llm.model_config import is_local_mode

    if not await run_blocking(db_executor, is_local_mode):
        return []
    if not await run_blocking(db_executor, device_calendar_db.has_events, uid):
        return []
    return [get_device_calendar_events_tool]
