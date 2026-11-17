"""What waits for the user's OK, and the optional auto-approve (omi-local).

Three things wait: tasks extracted from conversations (utils/task_approval.py), events
heard in conversations (the Calendar check, utils/family_calendar.py) and day summary
drafts (utils/day_review_service.py). ``waiting_summary`` counts them for the app's
banner; ``approve_batch`` is the user's Approve all / Approve selected (any of the three,
optionally of one day) and ``undo_batch`` its Undo. Nothing is approved on its own unless the user turns auto-approve on
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import database.approval_settings as approval_db
import database.notifications as notification_db
from utils import day_review_service, family_calendar, outbound_dispatch, task_approval
from utils.caldav import sync as caldav_sync
from utils.executors import db_executor, run_blocking

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
            # Auto-approved: a Connection sends it only where the user allowed that (outbound_dispatch).
            day_review_service.approve(uid, date_str, approved_by=outbound_dispatch.APPROVED_BY_AUTO)
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
        'events': events,
        'draft_dates': list(days.get('draft_dates') or []),
        'today': days.get('today') or '',
        'auto_approve': bool(settings.get('auto_approve')) and operator_allows(),
        'auto_approve_time': str(settings.get('time') or ''),
    }


# ---- Approve all / Approve selected (the user's gesture) -----------------------------------------
#
# One batch covers what the user chose: by default everything waiting (tasks, heard events and
# day summary drafts, optionally of one local day), or the items they left ticked. It answers
# with exactly what it approved, and ``undo_batch`` takes exactly that set back: tasks and events
# wait again, and a day goes back to its draft as it was (utils/day_review_service.undo_approval).
# Each batch marks the user's CalDAV sync dirty once.


Window = Optional[Tuple[datetime, datetime]]
EventRef = Tuple[str, int]


def _day_window(uid: str, date_str: Optional[str]) -> Window:
    """The user's local day [start, end] in UTC, or None (every day)."""
    if not date_str:
        return None
    day = day_review_service.parse_date(date_str)
    return day_review_service.day_bounds_utc(day, notification_db.get_user_time_zone(uid))


def _within(value: Any, window: Window) -> bool:
    if window is None:
        return True
    if not isinstance(value, datetime):
        return False
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return window[0] <= value <= window[1]


def waiting_items(uid: str, date_str: Optional[str] = None) -> Dict[str, Any]:
    """Everything waiting (of one local day with ``date_str``): tasks created then, events heard
    then, and day summary drafts (that day's)."""
    window = _day_window(uid, date_str)
    tasks = [str(t['id']) for t in task_approval.waiting(uid) if _within(t.get('created_at'), window)]
    events, _ = family_calendar.pending(uid)
    drafts = list(day_review_service.pending_dates(uid, days=8).get('draft_dates') or [])
    return {
        'task_ids': tasks,
        'events': [(e.conversation_id, e.event_idx) for e in events if _within(e.heard_at, window)],
        'day_dates': [d for d in drafts if not date_str or d == date_str],
    }


def _approve_days(uid: str, dates: List[str]) -> Tuple[List[str], List[str]]:
    done: List[str] = []
    failed: List[str] = []
    for date_str in dates:
        try:
            day_review_service.approve(uid, date_str)
        except day_review_service.DayReviewError as error:
            logger.warning('approve_batch: day %s %s: %s', uid, date_str, error.message)
            failed.append(date_str)
            continue
        done.append(date_str)
    return done, failed


async def approve_batch(
    uid: str,
    *,
    all_waiting: bool = False,
    date_str: Optional[str] = None,
    task_ids: Sequence[str] = (),
    events: Sequence[EventRef] = (),
    day_dates: Sequence[str] = (),
) -> Dict[str, Any]:
    """Approve, as if the user tapped Approve on each: everything waiting (``all_waiting``, of
    one local day with ``date_str``), or the listed items. Only what is still waiting is approved,
    so a second call approves nothing. Returns exactly what was approved, for Undo."""
    found = await run_blocking(db_executor, waiting_items, uid, date_str)
    if all_waiting:
        chosen_tasks, chosen_events, chosen_days = found['task_ids'], found['events'], found['day_dates']
    else:
        waiting_events = set(found['events'])
        waiting_days = set(found['day_dates'])
        chosen_tasks = list(dict.fromkeys(task_ids))
        chosen_events = [e for e in dict.fromkeys(events) if e in waiting_events]
        chosen_days = [d for d in dict.fromkeys(day_dates) if d in waiting_days]
    approved_events: List[Dict[str, Any]] = []
    failed_events = 0
    with caldav_sync.one_mark():
        approved_tasks = await run_blocking(db_executor, task_approval.approve_many, uid, chosen_tasks)
        for conversation_id, event_idx in chosen_events:
            try:
                await family_calendar.approve(uid, conversation_id, event_idx)
            except Exception as error:  # noqa: BLE001 — the gateway may refuse one; it stays waiting
                failed_events += 1
                logger.warning('approve_batch: event %s/%s failed: %s', uid, conversation_id, type(error).__name__)
                continue
            approved_events.append({'conversation_id': conversation_id, 'event_idx': event_idx})
        approved_days, failed_days = await run_blocking(db_executor, _approve_days, uid, chosen_days)
    logger.info(
        'approve_batch: uid=%s date=%s tasks=%d events=%d days=%d failed_events=%d failed_days=%d',
        uid,
        date_str or '-',
        len(approved_tasks),
        len(approved_events),
        len(approved_days),
        failed_events,
        len(failed_days),
    )
    return {
        'task_ids': approved_tasks,
        'events': approved_events,
        'day_dates': approved_days,
        'task_count': len(approved_tasks),
        'event_count': len(approved_events),
        'day_count': len(approved_days),
        'failed_event_count': failed_events,
        'failed_day_dates': failed_days,
    }


def undo_batch(
    uid: str, task_ids: Sequence[str], events: Sequence[EventRef], day_dates: Sequence[str] = ()
) -> Dict[str, int]:
    """Undo a batch, given exactly what it approved: tasks and events wait again and come off the
    user's CalDAV server at the next sync; each day goes back to its draft as it was. What cannot
    be taken back here (an event already on the family calendar, a day approved too long ago) is
    counted as kept."""
    tasks = 0
    reverted = 0
    days = 0
    kept = 0
    with caldav_sync.one_mark():
        for task_id in dict.fromkeys(task_ids):
            if task_approval.take_back(uid, task_id):
                tasks += 1
        for conversation_id, event_idx in dict.fromkeys(events):
            try:
                taken = family_calendar.take_back(uid, conversation_id, event_idx)
            except LookupError:
                continue
            if taken:
                reverted += 1
            elif taken is False:
                kept += 1
        for date_str in dict.fromkeys(day_dates):
            try:
                day_review_service.undo_approval(uid, date_str)
            except day_review_service.DayReviewError as error:
                logger.warning('undo_batch: day %s %s: %s', uid, date_str, error.message)
                kept += 1
                continue
            days += 1
    return {'task_count': tasks, 'event_count': reverted, 'day_count': days, 'kept_count': kept}
