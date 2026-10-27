"""Device-calendar snapshots pushed by the Android app (self-hosting).

The app reads CalendarContract (Google, DAVx5/CalDAV, Exchange, local
calendars) and pushes the window it read to POST /v1/device-calendar/sync; the
agentic chat's device-calendar tool reads it back.

Stored in Redis rather than process memory: the original in-memory dict was
emptied by every backend restart (each `docker compose up`, each model-picker
redeploy), so the chat tool reported "no events" until the phone happened to
sync again. The snapshot expires after a week without a sync.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import database.redis_db as redis_db

SNAPSHOT_TTL_SECONDS = 7 * 24 * 3600


# Two snapshots per user: the phone's (source "device") and the CalDAV
# pull's (source "caldav", utils/caldav/sync.py). Reads merge both.
SOURCES = ('device', 'caldav')


def _key(uid: str, source: str = 'device') -> str:
    return f'device_calendar:{uid}' if source == 'device' else f'device_calendar:{uid}:{source}'


def store_events(uid: str, events: List[Dict[str, Any]], *, source: str = 'device') -> None:
    redis_db.r.set(_key(uid, source), json.dumps(events), ex=SNAPSHOT_TTL_SECONDS)


def _load(uid: str, source: str) -> List[Dict[str, Any]]:
    raw = redis_db.r.get(_key(uid, source))
    if not raw:
        return []
    try:
        events = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return events if isinstance(events, list) else []


def get_events(uid: str, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Synced events overlapping [start_ms, end_ms]; [] when nothing was synced."""
    events: List[Dict[str, Any]] = []
    for source in SOURCES:
        events.extend(_load(uid, source))
    if not events:
        return []
    events = [e for e in events if isinstance(e, dict)]  # type: ignore[reportUnnecessaryIsInstance]  # Redis payload
    if start_ms is not None:
        events = [e for e in events if e.get('end_ms', 0) >= start_ms]
    if end_ms is not None:
        events = [e for e in events if e.get('start_ms', 0) <= end_ms]
    return events


def has_events(uid: str) -> bool:
    return bool(get_events(uid))
