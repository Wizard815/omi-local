"""What waits for the user's OK, and the optional auto-approve (omi-local).

Three things wait: tasks extracted from conversations (utils/task_approval.py), events
heard in conversations (the Calendar check, utils/family_calendar.py) and day summary
drafts (utils/day_review_service.py). ``waiting_summary`` counts them for the app's
banner. Nothing is approved on its own unless the user turns auto-approve on
(database/approval_settings.py, off by default for everyone): then, once a day after
the user's chosen local time, the hourly scheduler approves what is still waiting from
past days, as if the user had tapped Approve on each (one switch covers all three).

OMI_AUTO_APPROVE=off is the operator's switch to keep auto-approve off for everyone,
whatever users chose.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional

import database.approval_settings as approval_db
import database.notifications as notification_db
from utils import day_review_service, family_calendar, task_approval

logger = logging.getLogger(__name__)


def operator_allows() -> bool:
    return os.getenv('OMI_AUTO_APPROVE', '').strip().lower() not in {'0', 'off', 'false', 'no'}


def parse_time(value: str) -> Optional[time]:
    try:
        hour, minute = (int(p) for p in str(value or '').split(':', 1))
        return time(hour=hour, minute=minute)
    except (TypeError, ValueError):
        return None


def is_due(settings: Dict[str, Any], now_local: datetime) -> bool:
    """On, past the chosen local time, and not yet run for today's date."""
    if not settings.get('auto_approve') or not operator_allows():
        return False
    at = parse_time(str(settings.get('time') or ''))
    if at is None or now_local.time() < at:
        return False
    return str(settings.get('last_run_date') or '') != now_local.date().isoformat()


def _start_of_today_utc(now_local: datetime) -> datetime:
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def approve_past_events(uid: str, before: datetime) -> List[str]:
    """Every heard event still waiting whose conversation was on a past day."""
    waiting, _ = family_calendar.pending(uid)
    done: List[str] = []
    for event in waiting:
        if event.heard_at is None or event.heard_at >= before:
            continue
        try:
            asyncio.run(family_calendar.approve(uid, event.conversation_id, event.event_idx))
        except Exception as error:  # noqa: BLE001 — the gateway may be down; it stays waiting
            logger.warning('auto_approve: event %s/%s failed: %s', uid, event.conversation_id, type(error).__name__)
            continue
        done.append(f'{event.conversation_id}#{event.event_idx}')
    return done


def approve_past_days(uid: str, today: str) -> List[str]:
    done: List[str] = []
    for date_str in day_review_service.pending_dates(uid, days=8).get('draft_dates') or []:
        if date_str >= today:
            continue
        try:
            day_review_service.approve(uid, date_str)
        except day_review_service.DayReviewError as error:
            logger.warning('auto_approve: day %s %s: %s', uid, date_str, error.message)
            continue
        done.append(date_str)
    return done


def run_for_user(uid: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Approve the past days' waiting items when this user's auto-approve is due. {} otherwise."""
    settings = approval_db.get_settings(uid)
    tz = day_review_service.timezone_for(notification_db.get_user_time_zone(uid))
    now_local = now.astimezone(tz) if now is not None else datetime.now(tz)
    if not is_due(settings, now_local):
        return {}
    before = _start_of_today_utc(now_local)
    today = now_local.date().isoformat()
    outcome = {
        'tasks': task_approval.approve_waiting_before(uid, before),
        'events': approve_past_events(uid, before),
        'days': approve_past_days(uid, today),
    }
    approval_db.record_run(uid, today)
    logger.info(
        'auto_approve: uid=%s tasks=%d events=%d days=%d',
        uid,
        len(outcome['tasks']),
        len(outcome['events']),
        len(outcome['days']),
    )
    return outcome


def waiting_summary(uid: str) -> Dict[str, Any]:
    """What waits for the user's OK: tasks, heard events and day summary drafts."""
    tasks = task_approval.waiting(uid)
    events, _ = family_calendar.pending(uid)
    days = day_review_service.pending_dates(uid, days=7)
    settings = approval_db.get_settings(uid)
    return {
        'tasks': tasks,
        'task_count': len(tasks),
        'event_count': len(events),
        'draft_dates': list(days.get('draft_dates') or []),
        'today': days.get('today') or '',
        'auto_approve': bool(settings.get('auto_approve')) and operator_allows(),
        'auto_approve_time': str(settings.get('time') or ''),
    }
