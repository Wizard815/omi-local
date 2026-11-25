"""Conversations rolled into an approved day stay readable by chat (omi-local).

Approving a day (utils/day_review_service.finalize) discards the conversations it rolled
up into the day's summary and tags them ``day_review_date``. They are not dropped: the
user kept them as part of the day. Chat's retrieval hid every discarded conversation, so
"what did I promise Gary?" missed a promise made in a rolled-up conversation once the
day was approved. A discarded conversation listed in its approved day's
``rolled_up_conversation_ids`` counts as visible for chat; the ones the user dropped
(``dropped_conversation_ids``) and every other discarded conversation stay hidden.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, Optional, Set

import database.day_reviews as day_reviews_db

logger = logging.getLogger(__name__)

DAY_REVIEW_DATE_FIELD = 'day_review_date'
DRAFT = 'draft'


def rolled_up_ids(
    uid: str,
    rows: Iterable[Dict[str, Any]],
    *,
    get_review: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
) -> Set[str]:
    """Ids of the discarded rows that an approved day rolled up (one review read per day)."""
    reader = get_review or day_reviews_db.get_day_review
    by_date: Dict[str, Set[str]] = {}
    for row in rows:
        date = row.get(DAY_REVIEW_DATE_FIELD)
        if row.get('discarded') and isinstance(date, str) and date and row.get('id'):
            by_date.setdefault(date, set()).add(str(row['id']))
    found: Set[str] = set()
    for date, ids in by_date.items():
        try:
            review = reader(uid, date) or {}
        except Exception as error:  # noqa: BLE001 — unreadable review: keep them hidden as before
            logger.warning('day_rollup: review read failed uid=%s %s', uid, type(error).__name__)
            continue
        if str(review.get('status') or '') == DRAFT:
            continue
        found |= ids & {str(cid) for cid in review.get('rolled_up_conversation_ids') or []}
    return found


def hidden_from_chat(row: Dict[str, Any], rolled_up: Set[str]) -> bool:
    """Discarded, and not part of an approved day."""
    return bool(row.get('discarded')) and str(row.get('id')) not in rolled_up
