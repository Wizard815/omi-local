"""
Device calendar sync endpoint — accepts calendar data from the Android app.
The app queries CalendarContract (all calendars: Google, DAVx5/CalDAV, Exchange, local)
and pushes events to this endpoint for the backend's agentic tools to use.

Fully local: no Google API calls, no cloud calendar provider needed.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from utils.other import endpoints as auth

router = APIRouter()

# In-memory event cache keyed by uid. For a self-hosted setup with a single user,
# this is fine. For multi-user, would use Firestore or Redis.
_device_calendar_cache: Dict[str, List[Dict[str, Any]]] = {}


class DeviceCalendarEvent(BaseModel):
    eventId: int
    title: str
    startMs: int
    endMs: int
    description: Optional[str] = None
    location: Optional[str] = None
    calendarId: int
    allDay: bool = False
    calendarName: Optional[str] = None
    accountName: Optional[str] = None
    accountType: Optional[str] = None


class DeviceCalendarSyncRequest(BaseModel):
    events: List[DeviceCalendarEvent]
    calendars: Optional[List[Dict[str, Any]]] = None


class DeviceCalendarSyncResponse(BaseModel):
    synced: int
    message: str


@router.post("/v1/device-calendar/sync", response_model=DeviceCalendarSyncResponse)
def sync_device_calendar(
    request: DeviceCalendarSyncRequest,
    uid: str = Depends(auth.get_current_user_uid),
):
    """Accept calendar events from the phone's CalendarContract provider."""
    _device_calendar_cache[uid] = [
        {
            "event_id": str(e.eventId),
            "title": e.title,
            "start": datetime.fromtimestamp(e.startMs / 1000, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(e.endMs / 1000, tz=timezone.utc).isoformat(),
            "description": e.description or "",
            "location": e.location or "",
            "calendar_id": e.calendarId,
            "all_day": e.allDay,
            "calendar_name": e.calendarName or "",
            "account_name": e.accountName or "",
            "account_type": e.accountType or "",
            "start_ms": e.startMs,
            "end_ms": e.endMs,
        }
        for e in request.events
    ]
    return DeviceCalendarSyncResponse(
        synced=len(request.events),
        message=f"Synced {len(request.events)} device calendar events",
    )


@router.get("/v1/device-calendar/events")
def get_device_calendar_events(
    uid: str = Depends(auth.get_current_user_uid),
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
):
    """Get cached device calendar events, optionally filtered by time range."""
    events = _device_calendar_cache.get(uid, [])
    if start_ms is not None:
        events = [e for e in events if e["end_ms"] >= start_ms]
    if end_ms is not None:
        events = [e for e in events if e["start_ms"] <= end_ms]
    return {"events": events, "provider": "device_calendar", "count": len(events)}


def get_cached_device_calendar_events(
    uid: str,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Programmatic access for agentic tools. Returns [] if no device calendar data."""
    events = _device_calendar_cache.get(uid, [])
    if start_ms is not None:
        events = [e for e in events if e["end_ms"] >= start_ms]
    if end_ms is not None:
        events = [e for e in events if e["start_ms"] <= end_ms]
    return events


def has_device_calendar(uid: str) -> bool:
    """Check if device calendar data has been synced for this user."""
    return uid in _device_calendar_cache and len(_device_calendar_cache.get(uid, [])) > 0