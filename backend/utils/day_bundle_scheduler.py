"""Overnight day summary drafts, and the optional auto-approve (omi-local).

Every hour (utils/other/local_scheduler.py) each active user\'s unreviewed past
days get a summary DRAFT (utils/day_review_service.prepare_draft): the starred
conversations would stay, everything else is written into one sectioned day
summary, and the user reviews and approves it in the morning. The job never
approves a day: no conversation is rolled up and nothing goes to the calendar
until the user taps Approve. A day is due once the user\'s local clock has
passed OMI_DAY_BUNDLE_HOUR (default 3), so the night\'s last clip is in.

OMI_DAY_BUNDLE_AUTO: on | off (default: on when the hold is on) switches the
draft preparation. A day whose draft failed (the model was down) is retried
after RETRY_AFTER_SECONDS.

Auto-approve is the user\'s own setting (utils/auto_approve.py, off by default for
everyone): the same tick approves what still waits from past days once the
user\'s chosen time has passed.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

import database.day_bundle_users as active_users_db
import database.notifications as notification_db
import database.redis_db as redis_db
from config import conversation_hold
from utils import auto_approve
from utils import day_review_service as service

logger = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 6 * 3600
LOOKBACK_DAYS = 7  # past days the scheduler still bundles; today is never bundled, so the window is one day wider


def auto_enabled() -> bool:
    raw = os.getenv('OMI_DAY_BUNDLE_AUTO', '').strip().lower()
    if raw in {'1', 'on', 'true', 'yes'}:
        return True
    if raw in {'0', 'off', 'false', 'no'}:
        return False
    return conversation_hold.hold_enabled()


def bundle_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv('OMI_DAY_BUNDLE_HOUR') or 3)))
    except ValueError:
        return 3


def due_dates(uid: str, now_local: datetime, pending: List[str]) -> List[str]:
    """Pending dates before today (oldest first) once the bundle hour has passed; today is never bundled."""
    if now_local.hour < bundle_hour():
        return []
    today = now_local.date().isoformat()
    return sorted(d for d in pending if d < today)


def _failed_key(uid: str, date_str: str) -> str:
    return f'day_bundle:failed:{uid}:{date_str}'


def bundle_user(uid: str, *, now: Optional[datetime] = None) -> Dict[str, str]:
    """Write a draft for every due day of one user. Returns {date: outcome}."""
    tz_name = notification_db.get_user_time_zone(uid)
    now_local = now if now is not None else datetime.now(service.timezone_for(tz_name))
    # pending_dates counts today as its first day, so LOOKBACK_DAYS past days need one more
    pending_info = service.pending_dates(uid, days=LOOKBACK_DAYS + 1)
    drafted = set(pending_info.get('draft_dates') or [])
    # A day that already has a draft waits for the user; only days without one get a draft.
    pending = [d for d in (pending_info.get('pending_dates') or []) if d not in drafted]
    outcomes: Dict[str, str] = {}
    for date_str in due_dates(uid, now_local, pending):
        try:
            if redis_db.r.exists(_failed_key(uid, date_str)):
                outcomes[date_str] = 'backoff'
                continue
        except Exception:  # noqa: BLE001 — no Redis answer: just try
            pass
        try:
            service.prepare_draft(uid, date_str)
            outcomes[date_str] = 'drafted'
        except service.DayReviewError as error:
            outcomes[date_str] = f'error:{error.status_code}'
            logger.warning('day_bundle: uid=%s date=%s %s', uid, date_str, error.message)
            try:
                redis_db.r.set(_failed_key(uid, date_str), '1', ex=RETRY_AFTER_SECONDS)
            except Exception:  # noqa: BLE001
                pass
    return outcomes


def run_once(
    *, users: Optional[Callable[[], List[str]]] = None, now: Optional[datetime] = None
) -> Dict[str, Dict[str, str]]:
    """Drafts for the due days of every active user (when OMI_DAY_BUNDLE_AUTO allows), then each
    user's auto-approve when they turned it on and its time has come."""
    results: Dict[str, Dict[str, str]] = {}
    drafts = auto_enabled()
    for uid in (users or active_users_db.active_users)():
        if drafts:
            try:
                outcome = bundle_user(uid, now=now)
            except Exception:  # noqa: BLE001 — one user never stops the others
                logger.exception('day_bundle: uid=%s failed', uid)
                outcome = {}
            if outcome:
                results[uid] = outcome
                logger.info('day_bundle: uid=%s %s', uid, outcome)
        try:
            approved = auto_approve.run_for_user(uid, now=now)
        except Exception:  # noqa: BLE001
            logger.exception('auto_approve: uid=%s failed', uid)
            continue
        if approved:
            results.setdefault(uid, {})[
                'auto_approved'
            ] = f"tasks={len(approved['tasks'])} events={len(approved['events'])} days={len(approved['days'])}"
    return results


async def tick() -> None:
    from utils.executors import run_blocking, db_executor

    await run_blocking(db_executor, run_once)
