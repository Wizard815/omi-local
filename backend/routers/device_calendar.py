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

import database.device_calendar as device_calendar_db
from utils.other import endpoints as auth

router = APIRouter()


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
    events = [
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
    device_calendar_db.store_events(uid, events)
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
    """Get synced device calendar events, optionally filtered by time range."""
    events = device_calendar_db.get_events(uid, start_ms=start_ms, end_ms=end_ms)
    return {"events": events, "provider": "device_calendar", "count": len(events)}
