"""The outbound queue's worker (omi-local): sends what is due, if the user's OK still stands.

Runs every few seconds from the local scheduler (utils/other/local_scheduler.py). For each
item of database/outbound_work.py:

- ``wiki_day`` / ``orb_day``: once ``due_at`` passed, and only if the day is still approved by
  the same approval (an Undo or a reopen since drops it), send the day summary / Orb line;
- ``wiki_conv``: once due, and only while the clip is still kept (starred, not discarded) and
  processed, send it (a clip still being processed waits);
- ``job``: follow an agent job (utils/agent_jobs.py).

A send that fails for a transient reason is retried a few times, then recorded as failed on
the receipt; an item whose target was turned off is dropped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.outbound_work as work_db
from utils import agent_jobs, day_review_service, mcp_targets, wiki_outbound
from utils.executors import db_executor, run_blocking
from utils.outbound_http import OutboundError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
RETRY_AFTER = timedelta(minutes=2)
TRANSIENT = ('unreachable', 'http')
CONVERSATION_WAIT_LIMIT = 30  # ticks of "still processing" before giving up on a kept clip


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _same_approval(uid: str, date_str: str, approved_at: Any) -> bool:
    review = day_reviews_db.get_day_review(uid, date_str)
    if not review or day_review_service.is_draft(review):
        return False
    return _aware(review.get('approved_at')) == _aware(approved_at)


def _conversation_state(uid: str, conversation_id: str) -> str:
    """'ready', 'wait' (still being processed) or 'gone' (deleted, discarded or no longer kept)."""
    row = conversations_db.get_conversation(uid, conversation_id)
    if not row or row.get('deleted') or row.get('discarded') or not row.get('starred'):
        return 'gone'
    if row.get('processing_deferred') or str(row.get('status') or '') != 'completed':
        return 'wait'
    return 'ready'


async def _retry_or_drop(item: Dict[str, Any], now: datetime, error: str) -> None:
    attempts = int(item.get('attempts') or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        logger.warning('outbound: giving up on %s after %d attempts: %s', item.get('key'), attempts, error)
        await run_blocking(db_executor, work_db.delete, item['key'])
        return
    await run_blocking(
        db_executor,
        work_db.update,
        item['key'],
        {'attempts': attempts, 'due_at': now + RETRY_AFTER, 'last_error': error[:300]},
    )


async def _day(item: Dict[str, Any], now: datetime, send: Callable[[str, str], Awaitable[Dict[str, Any]]]) -> str:
    uid, date_str = item['uid'], item['date']
    if not await run_blocking(db_executor, _same_approval, uid, date_str, item.get('approved_at')):
        await run_blocking(db_executor, work_db.delete, item['key'])
        return 'dropped'
    try:
        receipt = await send(uid, date_str)
    except (wiki_outbound.WikiNotReady, mcp_targets.TargetNotReady):
        await run_blocking(db_executor, work_db.delete, item['key'])
        return 'dropped'
    if receipt.get('error') and receipt.get('error_kind') in TRANSIENT:
        await _retry_or_drop(item, now, receipt['error'])
        return 'retry'
    await run_blocking(db_executor, work_db.delete, item['key'])
    return 'sent' if not receipt.get('error') else 'failed'


async def _kept_conversation(item: Dict[str, Any], now: datetime) -> str:
    uid, cid = item['uid'], item['conversation_id']
    state = await run_blocking(db_executor, _conversation_state, uid, cid)
    if state == 'gone':
        await run_blocking(db_executor, work_db.delete, item['key'])
        return 'dropped'
    if state == 'wait':
        waits = int(item.get('waits') or 0) + 1
        if waits > CONVERSATION_WAIT_LIMIT:
            await run_blocking(db_executor, work_db.delete, item['key'])
            return 'dropped'
        await run_blocking(db_executor, work_db.update, item['key'], {'waits': waits, 'due_at': now + RETRY_AFTER})
        return 'waiting'
    try:
        receipt, already = await wiki_outbound.send_conversation(uid, cid)
    except wiki_outbound.WikiNotReady:
        await run_blocking(db_executor, work_db.delete, item['key'])
        return 'dropped'
    except OutboundError as error:
        await _retry_or_drop(item, now, error.message)
        return 'retry'
    await run_blocking(db_executor, work_db.delete, item['key'])
    if already:
        return 'already_sent'
    return 'sent' if receipt and not receipt.get('error') else 'failed'


async def run_due(now: Optional[datetime] = None) -> Dict[str, str]:
    """Process every due item once. Returns {key: outcome} (for logs and tests)."""
    now = now or datetime.now(timezone.utc)
    items = await run_blocking(db_executor, work_db.all_items)
    outcomes: Dict[str, str] = {}
    for item in items:
        key, kind = item.get('key'), item.get('kind')
        if not key:
            continue
        due = _aware(item.get('due_at'))
        if due is not None and due > now:
            continue
        try:
            if kind == 'wiki_day':
                outcomes[key] = await _day(item, now, wiki_outbound.send_day)
            elif kind == 'orb_day':
                outcomes[key] = await _day(item, now, mcp_targets.orb_log_day)
            elif kind == 'wiki_conv':
                outcomes[key] = await _kept_conversation(item, now)
            elif kind == 'job':
                outcomes[key] = await agent_jobs.follow(item, now)
        except OutboundError as error:
            await _retry_or_drop(item, now, error.message)
            outcomes[key] = 'retry'
        except Exception as error:  # noqa: BLE001 — one bad item never stops the others
            logger.exception('outbound: %s failed', key)
            await _retry_or_drop(item, now, type(error).__name__)
            outcomes[key] = 'error'
    if outcomes:
        logger.info('outbound tick: %s', sorted(set(outcomes.values())))
    return outcomes


async def tick() -> None:
    await run_due()
