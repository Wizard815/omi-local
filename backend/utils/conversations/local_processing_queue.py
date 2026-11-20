"""Self-hosted background processing queue: which stored conversations still need notes.

A slow local model must not hold the upload request or lose the conversation. The
upload stores the transcript as a deferred row (``status=processing``,
``deferred=True``) and enqueues it here; ``utils.local_processing_worker`` writes
the notes in the background and retries with backoff.

The queue is a Redis sorted set (member ``uid/conversation_id``, score = due time)
plus an attempt counter. It is only a schedule: the deferred row is the durable
record. If Redis is down or loses the entry, the row stays deferred and the
existing first-open enrichment (``GET /v1/conversations/{id}``) still processes it.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

import database.redis_db as redis_db
from config.local_llm import local_background_processing_enabled
from utils.observability.fallback import record_fallback
from utils.other.local_scheduler import local_scheduler_enabled

logger = logging.getLogger(__name__)

DUE_KEY = 'omi:local_processing:due'
ATTEMPTS_KEY = 'omi:local_processing:attempts'
# First attempt waits for the upload's own final write to land.
FIRST_ATTEMPT_DELAY_SECONDS = 3.0


def enabled() -> bool:
    """Background processing needs a local model and the in-process scheduler that runs the worker."""
    return local_background_processing_enabled() and local_scheduler_enabled()


def _member(uid: str, conversation_id: str) -> str:
    return f'{uid}/{conversation_id}'


def enqueue(uid: str, conversation_id: str, *, delay_seconds: float = FIRST_ATTEMPT_DELAY_SECONDS) -> bool:
    """Schedule (or reschedule) one conversation. Never raises; False when Redis refused."""
    try:
        redis_db.r.zadd(DUE_KEY, {_member(uid, conversation_id): time.time() + max(0.0, delay_seconds)})
        return True
    except Exception as exc:
        record_fallback(
            component='other',
            from_mode='local_background_processing',
            to_mode='first_open_enrichment',
            reason='enqueue_failed',
            outcome='degraded',
            log=logger,
        )
        logger.warning(
            'local_processing enqueue failed uid=%s conversation=%s error=%s', uid, conversation_id, type(exc).__name__
        )
        return False


def owns(conversation: dict) -> bool:
    """Whether the background worker, not a detail open, should process this row.

    A deferred row still ``processing`` is queued or between retries; opening it must
    not start a second, parallel model run against a slow local server. Once the worker
    gives up the row is ``completed`` and deferred, and first-open enrichment takes over.
    """
    return enabled() and conversation.get('deferred') is True and conversation.get('status') == 'processing'


def ensure_queued(uid: str, conversation_id: str) -> None:
    """Re-add a worker-owned row Redis lost (restart, flush); a queued or running one is left alone."""
    try:
        if redis_db.r.zscore(DUE_KEY, _member(uid, conversation_id)) is None:
            redis_db.r.zadd(DUE_KEY, {_member(uid, conversation_id): time.time()}, nx=True)
    except Exception as exc:
        logger.warning('local_processing ensure_queued failed error=%s', type(exc).__name__)


def claim_due(limit: int, *, now: Optional[float] = None) -> List[Tuple[str, str, int]]:
    """Take up to ``limit`` due conversations as ``(uid, conversation_id, attempt)``.

    ``attempt`` is 1 for the first try. Removing the member is the claim, so two
    backend replicas never run the same conversation at once.
    """
    current = time.time() if now is None else now
    claimed: List[Tuple[str, str, int]] = []
    try:
        members = redis_db.r.zrangebyscore(DUE_KEY, '-inf', current, start=0, num=limit) or []
        for raw in members:
            member = raw.decode() if isinstance(raw, bytes) else str(raw)
            if not redis_db.r.zrem(DUE_KEY, member):
                continue
            uid, _, conversation_id = member.partition('/')
            if not uid or not conversation_id:
                continue
            attempt = int(redis_db.r.hincrby(ATTEMPTS_KEY, member, 1))
            claimed.append((uid, conversation_id, attempt))
    except Exception as exc:
        logger.warning('local_processing claim failed error=%s', type(exc).__name__)
    return claimed


def forget(uid: str, conversation_id: str) -> None:
    try:
        redis_db.r.hdel(ATTEMPTS_KEY, _member(uid, conversation_id))
    except Exception as exc:
        logger.warning('local_processing forget failed error=%s', type(exc).__name__)


def pending_count() -> int:
    try:
        return int(redis_db.r.zcard(DUE_KEY) or 0)
    except Exception:
        return 0
