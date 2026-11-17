"""What an approval or a keep queues for the user's Connections (omi-local).

Called from the day review (utils/day_review_service.py) right after the user's OK is
recorded. Nothing is sent here: each item goes into the outbound queue
(database/outbound_work.py) with a due time, and the scheduler tick sends it then
(utils/outbound_worker.py) only if the OK still stands (the same approval of the day, the
clip still kept). The delay (OMI_OUTBOUND_DAY_DELAY_SECONDS, default 10 minutes) lets
Approve all's Undo take a day back before anything has left.

Auto-approve counts as the user's OK only for a target whose ``require_manual_approval`` the
user switched off; otherwise an auto-approved day is not sent, and the day review says why.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import database.day_reviews as day_reviews_db
import database.outbound_work as work_db
from utils import integrations_out

logger = logging.getLogger(__name__)

DELAY_ENV = 'OMI_OUTBOUND_DAY_DELAY_SECONDS'
DEFAULT_DELAY_SECONDS = 600.0
APPROVED_BY_USER = 'user'
APPROVED_BY_AUTO = 'auto'
AUTO_NOT_SENT = 'The day was approved automatically, and this connection sends only days you approve'


def delay() -> timedelta:
    raw = os.getenv(DELAY_ENV, '').strip()
    try:
        seconds = float(raw) if raw else DEFAULT_DELAY_SECONDS
    except ValueError:
        seconds = DEFAULT_DELAY_SECONDS
    return timedelta(seconds=max(0.0, seconds))


def day_key(target: str, uid: str, date_str: str) -> str:
    return f'{target}_day:{uid}:{date_str}'


def conversation_key(uid: str, conversation_id: str) -> str:
    return f'wiki_conv:{uid}:{conversation_id}'


def _receipt(target: str, **fields: Any) -> Dict[str, Any]:
    return {'target': target, 'sent_at': None, 'title': '', 'revision': 0, 'detail': '', 'error': '', **fields}


def mirror(uid: str, date_str: str, target: str, rows: List[Dict[str, Any]]) -> None:
    """Show a target's receipts for the day on the day review (replacing that target's rows)."""
    review = day_reviews_db.get_day_review(uid, date_str) or {}
    kept = [r for r in (review.get('outbound_receipts') or []) if r.get('target') != target]
    day_reviews_db.merge_day_review_fields(uid, date_str, {'outbound_receipts': [*kept, *rows]})


def day_approved(
    uid: str,
    date_str: str,
    review: Dict[str, Any],
    approved_by: str = APPROVED_BY_USER,
    *,
    now: Optional[datetime] = None,
) -> List[str]:
    """Queue what the user's Connections send for an approved day. Never raises: a broken
    setting must not undo the approval. Returns the queued keys."""
    try:
        return _day_approved(uid, date_str, review, approved_by, now=now)
    except Exception as error:  # noqa: BLE001 — the day is approved either way
        logger.warning('outbound: day %s %s not queued: %s', uid, date_str, type(error).__name__)
        return []


def _day_approved(
    uid: str, date_str: str, review: Dict[str, Any], approved_by: str, *, now: Optional[datetime]
) -> List[str]:
    settings = integrations_out.effective(uid)
    now = now or datetime.now(timezone.utc)
    due = now + delay()
    approved_at = review.get('approved_at')
    queued: List[str] = []
    for target, wanted in (
        ('wiki', bool(settings['wiki'].get('send_day_reviews'))),
        ('orb', bool(settings['orb'].get('log_day_approvals'))),
    ):
        row = settings[target]
        if not integrations_out.ready(row) or not wanted:
            continue
        if approved_by == APPROVED_BY_AUTO and row.get('require_manual_approval', True):
            mirror(uid, date_str, target, [_receipt(target, error=AUTO_NOT_SENT)])
            continue
        key = day_key(target, uid, date_str)
        work_db.put(
            key,
            {
                'kind': f'{target}_day',
                'uid': uid,
                'date': date_str,
                'approved_at': approved_at,
                'approved_by': approved_by,
                'due_at': due,
                'attempts': 0,
            },
        )
        mirror(uid, date_str, target, [_receipt(target, scheduled_at=due, detail='Waiting for the Undo window')])
        queued.append(key)
    wiki = settings['wiki']
    if integrations_out.ready(wiki) and wiki.get('send_kept_conversations') == 'on_keep':
        if approved_by != APPROVED_BY_AUTO or not wiki.get('require_manual_approval', True):
            queued.extend(_queue_conversations(uid, review.get('kept_conversation_ids') or [], due))
    return queued


def conversations_kept(uid: str, conversation_ids: Sequence[str], *, now: Optional[datetime] = None) -> List[str]:
    """The user kept these clips (a section or one clip pulled out of the day's summary): with
    "send when I keep a clip" on, each goes to the wiki once it is ready. Never raises."""
    try:
        wiki = integrations_out.effective(uid)['wiki']
        if not integrations_out.ready(wiki) or wiki.get('send_kept_conversations') != 'on_keep':
            return []
        return _queue_conversations(uid, conversation_ids, (now or datetime.now(timezone.utc)) + delay())
    except Exception as error:  # noqa: BLE001 — keeping the clip stands either way
        logger.warning('outbound: kept clips of %s not queued: %s', uid, type(error).__name__)
        return []


def _queue_conversations(uid: str, conversation_ids: Sequence[str], due: datetime) -> List[str]:
    keys = []
    for cid in dict.fromkeys(conversation_ids):
        key = conversation_key(uid, cid)
        work_db.put(key, {'kind': 'wiki_conv', 'uid': uid, 'conversation_id': cid, 'due_at': due, 'attempts': 0})
        keys.append(key)
    return keys
