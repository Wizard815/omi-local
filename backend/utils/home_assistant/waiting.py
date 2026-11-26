"""The scheduled Home Assistant events (omi-local): "waiting for your OK" and "task due".

``waiting_changed`` counts what waits for the user's OK (tasks, heard events and day summary
drafts as GET /v1/approvals counts them, Hermes questions, and Home Assistant actions) and
fires ``omi_waiting_for_approval`` when the count went up since the last one it announced
(stored per user), so an automation hears about new things to approve, not every recount.
A Home Assistant action that starts or stops waiting marks the user (actions.
mark_waiting_changed) and ``dirty_tick`` (every DIRTY_SECONDS) recounts them within seconds;
``tick`` (every TICK_SECONDS, utils/other/local_scheduler.py) catches everything else, such as
tasks a conversation produced.

``tick`` also fires ``omi_task_due`` for every approved, open task whose due time passed since
the user's last check (at most MAX_DUE per user per tick).

Users: the stack's active members (database/day_bundle_users.py, the set the day bundle and
CalDAV sync walk). Only users who turned the event on are read further.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import database.action_items as action_items_db
import database.day_bundle_users as active_users_db
import database.home_assistant as store
import database.redis_db as redis_db
from utils import auto_approve, task_approval
from utils.home_assistant.runner import db
from utils.home_assistant import events
from utils.home_assistant.actions import WAITING_DIRTY_KEY
from utils.home_assistant import settings as ha_settings
from utils.observability.fallback import record_fallback

logger = logging.getLogger(__name__)

TICK_SECONDS = 60.0
DIRTY_SECONDS = 5.0
MAX_DIRTY = 50
FIRST_WINDOW = timedelta(minutes=2)
LONGEST_WINDOW = timedelta(hours=6)
MAX_DUE = 10


def _skipped() -> None:
    record_fallback(
        component='webhook', from_mode='home_assistant', to_mode='none', reason='unavailable', outcome='degraded'
    )


def home_actions_waiting(uid: str) -> int:
    now = datetime.now(timezone.utc)
    return sum(
        1
        for row in store.list_actions(uid, limit=50)
        if row.get('status') == 'waiting_for_approval' and not ha_settings.expired(row, now)
    )


def count(uid: str) -> Dict[str, int]:
    summary = auto_approve.waiting_summary(uid)
    out = {
        'tasks': int(summary.get('task_count') or 0),
        'events': int(summary.get('event_count') or 0),
        'day_summaries': len(summary.get('draft_dates') or []),
        'agent_questions': len(summary.get('agent_jobs') or []),
        'home_actions': home_actions_waiting(uid),
    }
    out['count'] = sum(out.values())
    return out


async def waiting_changed(uid: str, *, settings: Optional[Dict[str, Any]] = None) -> None:
    """Announce a higher waiting count; remember the current one either way. Never raises."""
    try:
        settings = await events.settings_for(uid, settings)
        if not ha_settings.ready(settings) or not (settings.get('events') or {}).get('waiting_for_approval'):
            return
        counts = await db(count, uid)
        if counts['count'] > int(settings.get('waiting_count') or 0):
            await events.fire(uid, 'waiting_for_approval', counts, settings=settings)
        if counts['count'] != int(settings.get('waiting_count') or 0):
            await db(store.set_state, uid, {'waiting_count': counts['count']})
    except Exception as error:  # noqa: BLE001 — the caller (an action, the tick) carries on
        _skipped()
        logger.warning('home_assistant: waiting count failed uid=%s %s', uid, type(error).__name__)


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due_tasks(uid: str, since: datetime, now: datetime) -> List[Dict[str, Any]]:
    rows = action_items_db.get_action_items(
        uid, completed=False, due_start_date=since, due_end_date=now, limit=MAX_DUE * 2
    )
    return [r for r in rows if task_approval.is_approved(r) and not r.get('deleted')][:MAX_DUE]


async def tasks_due(uid: str, settings: Dict[str, Any], now: datetime) -> int:
    last = _aware(settings.get('due_checked_at'))
    since = max(last, now - LONGEST_WINDOW) if last else now - FIRST_WINDOW
    rows = await db(due_tasks, uid, since, now)
    minimal = settings.get('event_detail') == 'minimal'
    fired = 0
    for row in rows:
        due = _aware(row.get('due_at'))
        if due is None or not since < due <= now:
            continue
        payload: Dict[str, Any] = {'task_id': row.get('id'), 'due_at': due.isoformat()}
        if not minimal:
            payload['description'] = events.short(row.get('description'), events.TASK_CHARS)
        fired += int(await events.fire(uid, 'task_due', payload, settings=settings))
    await db(store.set_state, uid, {'due_checked_at': now})
    return fired


async def tick(*, now: Optional[datetime] = None) -> None:
    """One pass over the active members. One user's failure never stops the others."""
    now = now or datetime.now(timezone.utc)
    for uid in await db(active_users_db.active_users):
        try:
            settings = await db(ha_settings.effective, uid)
            if not settings.get('stored') and not ha_settings.env_events():
                continue
            if not ha_settings.ready(settings):
                continue
            wanted = settings.get('events') or {}
            if wanted.get('task_due'):
                await tasks_due(uid, settings, now)
            if wanted.get('waiting_for_approval'):
                await waiting_changed(uid, settings=settings)
        except Exception as error:  # noqa: BLE001
            _skipped()
            logger.warning('home_assistant: tick failed uid=%s %s', uid, type(error).__name__)


def _pop_dirty() -> List[str]:
    members = redis_db.r.spop(WAITING_DIRTY_KEY, MAX_DIRTY) or []
    return sorted({m.decode() if isinstance(m, bytes) else str(m) for m in members})


async def dirty_tick() -> None:
    """Recount the users whose Home Assistant actions just changed. Never raises."""
    try:
        users = await db(_pop_dirty)
    except Exception:  # noqa: BLE001 — the minute tick recounts everyone anyway
        _skipped()
        return
    for uid in users:
        await waiting_changed(uid)
