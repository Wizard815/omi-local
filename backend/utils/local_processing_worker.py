"""Self-hosted background worker: write notes for conversations stored before the model answered.

``utils.conversations.local_processing_queue`` schedules a deferred row; this worker
claims it, runs the normal processing pipeline, and on failure (a slow local model
timing out, the model server restarting) hands it back for a retry with backoff.
The transcript is never touched by a failure: the row stays ``processing`` and
deferred between attempts. After the last attempt it becomes a completed,
still-deferred row, which the existing first-open enrichment retries when the
user opens it.

One conversation runs at a time: a home model server answers one request at a
time anyway, and parallel attempts only make every one of them slower.
"""

from __future__ import annotations

import logging

from config.local_llm import local_processing_max_attempts, local_processing_retry_delay
from database import conversations as conversations_db
from models.conversation_enums import ConversationStatus
from utils.conversations import lifecycle as lifecycle_service
from utils.conversations import local_processing_queue as queue
from utils.conversations.factory import deserialize_conversation
from utils.conversations.meeting_receipt import record_finalized_meeting_receipt
from utils.conversations.process_conversation import AppUsageAttribution, process_conversation
from utils.conversations.processing_trigger import ProcessingTrigger
from utils.executors import db_executor, postprocess_executor, run_blocking
from utils.observability.fallback import record_fallback

logger = logging.getLogger(__name__)

TICK_SECONDS = 5.0
CLAIM_BATCH = 4

COMPLETED = 'completed'
SKIPPED = 'skipped'
RETRY = 'retry'
GAVE_UP = 'gave_up'


def run_one(uid: str, conversation_id: str, attempt: int) -> str:
    """Process one deferred conversation; returns the outcome. Blocking: call off the event loop."""
    row = conversations_db.get_conversation(uid, conversation_id)
    if not row or row.get('deleted') or row.get('discarded') or row.get('deferred') is not True:
        # Already processed (first open won the race), deleted, or discarded.
        return SKIPPED
    if not lifecycle_service.reacquire_deferred_processing(uid, conversation_id):
        return SKIPPED
    try:
        conversation = deserialize_conversation(row)
        conversation.deferred = False
        conversation.status = ConversationStatus.processing
        with lifecycle_service.processing_admission_guard(uid, conversation_id, rollback_on_failure=False):
            processed = process_conversation(
                uid,
                conversation.language or 'en',
                conversation,
                app_usage_attribution=AppUsageAttribution.AUTOMATIC_PROCESSING,
                trigger=ProcessingTrigger.CLIENT_FINALIZE,
            )
        try:
            record_finalized_meeting_receipt(uid, processed)
        except Exception:
            logger.exception('local_processing receipt failed uid=%s conversation=%s', uid, conversation_id)
        logger.info('local_processing completed uid=%s conversation=%s attempt=%d', uid, conversation_id, attempt)
        return COMPLETED
    except Exception as exc:
        final = attempt >= local_processing_max_attempts()
        logger.warning(
            'local_processing attempt failed uid=%s conversation=%s attempt=%d final=%s error=%s',
            uid,
            conversation_id,
            attempt,
            final,
            type(exc).__name__,
        )
        record_fallback(
            component='other',
            from_mode='local_background_processing',
            to_mode='first_open_enrichment' if final else 'local_background_retry',
            reason='other',
            outcome='degraded',
            log=logger,
        )
        try:
            if final:
                lifecycle_service.recover_deferred_processing_failure(uid, conversation_id)
            else:
                lifecycle_service.rearm_deferred_processing(uid, conversation_id)
        except Exception:
            logger.exception('local_processing re-arm failed uid=%s conversation=%s', uid, conversation_id)
        return GAVE_UP if final else RETRY


async def tick() -> None:
    """Run every due conversation, one at a time."""
    for uid, conversation_id, attempt in await run_blocking(db_executor, queue.claim_due, CLAIM_BATCH):
        outcome = await run_blocking(postprocess_executor, run_one, uid, conversation_id, attempt)
        if outcome == RETRY:
            delay = local_processing_retry_delay(attempt)
            await run_blocking(db_executor, queue.enqueue, uid, conversation_id, delay_seconds=delay)
        else:
            await run_blocking(db_executor, queue.forget, uid, conversation_id)
