"""Self-hosted background processing: a slow model never loses a stored conversation.

The queue (Redis sorted set) schedules deferred rows; the worker runs the normal pipeline,
re-arms the row for a retry with backoff when the model fails, and after the last attempt
leaves a completed, still-deferred row that first-open enrichment retries.
"""

import asyncio
import time
from unittest.mock import MagicMock

import fakeredis
import pytest

from config import local_llm
from database import conversation_finalization_jobs as jobs_db
from utils import local_processing_worker as worker
from utils.conversations import local_processing_queue as queue

# Imported here so the router's import time is paid at collection, not inside one test.
import routers.conversations as conversations_router  # noqa: E402


class _ModelTimedOut(Exception):
    """Stands in for openai.APITimeoutError from a slow local model."""


@pytest.fixture
def redis(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(queue.redis_db, 'r', fake)
    return fake


@pytest.fixture
def lifecycle(monkeypatch):
    spies = MagicMock()
    spies.reacquire_deferred_processing.return_value = True
    spies.rearm_deferred_processing.return_value = True
    spies.recover_deferred_processing_failure.return_value = True
    spies.processing_admission_guard.return_value.__enter__ = lambda *a: None
    spies.processing_admission_guard.return_value.__exit__ = lambda *a: False
    monkeypatch.setattr(worker, 'lifecycle_service', spies)
    monkeypatch.setattr(worker, 'record_finalized_meeting_receipt', MagicMock())
    monkeypatch.setattr(worker, 'deserialize_conversation', lambda row: MagicMock(language='en'))
    return spies


def _row(**overrides):
    row = {'id': 'c1', 'status': 'processing', 'deferred': True, 'discarded': False}
    row.update(overrides)
    return row


def test_enabled_needs_a_local_model_and_the_scheduler(monkeypatch):
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://proxy/v1')
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setenv('OMI_LOCAL_SCHEDULER', '1')
    monkeypatch.delenv(local_llm.BACKGROUND_ENV, raising=False)
    assert queue.enabled() is True
    monkeypatch.setenv(local_llm.BACKGROUND_ENV, 'off')
    assert queue.enabled() is False
    monkeypatch.delenv(local_llm.BACKGROUND_ENV)
    monkeypatch.setenv('OMI_LOCAL_SCHEDULER', '0')
    assert queue.enabled() is False
    monkeypatch.setenv('OMI_LOCAL_SCHEDULER', '1')
    monkeypatch.delenv('OPENAI_BASE_URL')
    assert queue.enabled() is False


def test_retry_schedule_backs_off_and_is_bounded(monkeypatch):
    monkeypatch.delenv(local_llm.MAX_ATTEMPTS_ENV, raising=False)
    assert local_llm.local_processing_max_attempts() == 5
    assert [local_llm.local_processing_retry_delay(n) for n in (1, 2, 3, 4, 9)] == [60, 300, 900, 3600, 3600]
    monkeypatch.setenv(local_llm.MAX_ATTEMPTS_ENV, '2')
    assert local_llm.local_processing_max_attempts() == 2


def test_queue_claims_due_items_once_and_counts_attempts(redis):
    queue.enqueue('u1', 'c1', delay_seconds=0)
    queue.enqueue('u1', 'c2', delay_seconds=600)
    assert queue.claim_due(10, now=time.time() + 1) == [('u1', 'c1', 1)]
    assert queue.claim_due(10, now=time.time() + 1) == []
    queue.enqueue('u1', 'c1', delay_seconds=0)
    assert queue.claim_due(10, now=time.time() + 1) == [('u1', 'c1', 2)]
    queue.forget('u1', 'c1')
    queue.enqueue('u1', 'c1', delay_seconds=0)
    assert queue.claim_due(10, now=time.time() + 1) == [('u1', 'c1', 1)]
    assert queue.pending_count() == 1


def test_enqueue_never_raises_when_redis_is_down(monkeypatch):
    broken = MagicMock()
    broken.zadd.side_effect = ConnectionError('redis down')
    monkeypatch.setattr(queue.redis_db, 'r', broken)
    assert queue.enqueue('u1', 'c1') is False


def test_success_runs_the_normal_pipeline(monkeypatch, lifecycle):
    process = MagicMock()
    monkeypatch.setattr(worker.conversations_db, 'get_conversation', lambda uid, cid: _row())
    monkeypatch.setattr(worker, 'process_conversation', process)

    assert worker.run_one('u1', 'c1', 1) == worker.COMPLETED
    process.assert_called_once()
    assert process.call_args.kwargs['trigger'] == worker.ProcessingTrigger.CLIENT_FINALIZE
    assert 'local_background' not in process.call_args.kwargs
    lifecycle.rearm_deferred_processing.assert_not_called()


def test_model_timeout_keeps_the_row_processing_for_a_retry(monkeypatch, lifecycle):
    monkeypatch.setattr(worker.conversations_db, 'get_conversation', lambda uid, cid: _row())
    monkeypatch.setattr(worker, 'process_conversation', MagicMock(side_effect=_ModelTimedOut()))

    assert worker.run_one('u1', 'c1', 1) == worker.RETRY
    lifecycle.rearm_deferred_processing.assert_called_once_with('u1', 'c1')
    lifecycle.recover_deferred_processing_failure.assert_not_called()


def test_last_attempt_hands_over_to_first_open(monkeypatch, lifecycle):
    monkeypatch.setenv(local_llm.MAX_ATTEMPTS_ENV, '3')
    monkeypatch.setattr(worker.conversations_db, 'get_conversation', lambda uid, cid: _row())
    monkeypatch.setattr(worker, 'process_conversation', MagicMock(side_effect=_ModelTimedOut()))

    assert worker.run_one('u1', 'c1', 3) == worker.GAVE_UP
    lifecycle.recover_deferred_processing_failure.assert_called_once_with('u1', 'c1')


@pytest.mark.parametrize(
    'row', [None, _row(deferred=False, status='completed'), _row(discarded=True), _row(deleted=True)]
)
def test_rows_that_no_longer_need_notes_are_skipped(monkeypatch, lifecycle, row):
    process = MagicMock()
    monkeypatch.setattr(worker.conversations_db, 'get_conversation', lambda uid, cid: row)
    monkeypatch.setattr(worker, 'process_conversation', process)
    assert worker.run_one('u1', 'c1', 1) == worker.SKIPPED
    process.assert_not_called()


def test_lost_reacquire_race_is_skipped(monkeypatch, lifecycle):
    lifecycle.reacquire_deferred_processing.return_value = False
    process = MagicMock()
    monkeypatch.setattr(worker.conversations_db, 'get_conversation', lambda uid, cid: _row())
    monkeypatch.setattr(worker, 'process_conversation', process)
    assert worker.run_one('u1', 'c1', 1) == worker.SKIPPED
    process.assert_not_called()


def test_tick_reschedules_a_failure_with_backoff_and_forgets_success(monkeypatch, redis):
    outcomes = {'c1': worker.RETRY, 'c2': worker.COMPLETED}

    async def inline(_executor, fn, *args, **kwargs):
        if fn is worker.run_one:
            return outcomes[args[1]]
        return fn(*args, **kwargs)

    monkeypatch.setattr(worker, 'run_blocking', inline)
    queue.enqueue('u1', 'c1', delay_seconds=0)
    queue.enqueue('u1', 'c2', delay_seconds=0)
    time.sleep(0.01)
    before = time.time()

    asyncio.run(worker.tick())

    due = dict(redis.zrange(queue.DUE_KEY, 0, -1, withscores=True))
    assert list(due) == [b'u1/c1']
    assert due[b'u1/c1'] >= before + 60
    assert redis.hget(queue.ATTEMPTS_KEY, 'u1/c1') == b'1'
    assert redis.hget(queue.ATTEMPTS_KEY, 'u1/c2') is None


@pytest.mark.parametrize(
    'data, rearmed',
    [
        ({'status': 'processing', 'deferred': False}, True),
        ({'status': 'completed', 'deferred': False}, False),
        ({'status': 'processing', 'deferred': True}, False),
        ({'status': 'processing', 'deferred': False, 'discarded': True}, False),
    ],
)
def test_rearm_only_takes_back_the_attempt_that_reacquired_the_row(data, rearmed):
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = data
    ref = MagicMock()
    ref.get.return_value = snapshot
    transaction = MagicMock()

    assert jobs_db._rearm_deferred_processing_txn(transaction, ref) is rearmed
    if rearmed:
        transaction.update.assert_called_once_with(ref, {'deferred': True})
    else:
        transaction.update.assert_not_called()


@pytest.mark.parametrize(
    'row, owned',
    [
        ({'deferred': True, 'status': 'processing'}, True),
        ({'deferred': True, 'status': 'completed'}, False),  # worker gave up: first open takes over
        ({'deferred': False, 'status': 'processing'}, False),  # an attempt is running
    ],
)
def test_worker_owns_only_queued_deferred_rows(monkeypatch, row, owned):
    monkeypatch.setattr(queue, 'enabled', lambda: True)
    assert queue.owns(row) is owned
    monkeypatch.setattr(queue, 'enabled', lambda: False)
    assert queue.owns(row) is False


def test_ensure_queued_restores_a_lost_entry_without_delaying_a_scheduled_retry(redis):
    queue.ensure_queued('u1', 'lost')
    assert redis.zscore(queue.DUE_KEY, 'u1/lost') is not None
    queue.enqueue('u1', 'retry', delay_seconds=900)
    due = redis.zscore(queue.DUE_KEY, 'u1/retry')
    queue.ensure_queued('u1', 'retry')
    assert redis.zscore(queue.DUE_KEY, 'u1/retry') == due


def test_opening_a_worker_owned_row_does_not_start_a_parallel_run(monkeypatch):
    """A detail read used to launch first-open enrichment for every queued upload at once."""
    row = {'id': 'c1', 'deferred': True, 'status': 'processing', 'source': 'omi'}
    monkeypatch.setattr(conversations_router, '_get_valid_conversation_by_id', lambda *a, **k: dict(row))
    monkeypatch.setattr(conversations_router, 'hidden_from_default_detail', lambda c: False)
    monkeypatch.setattr(conversations_router.conversations_db, 'is_visible_conversation', lambda *a, **k: True)
    monkeypatch.setattr(conversations_router.local_processing_queue, 'enabled', lambda: True)
    queued = MagicMock()
    monkeypatch.setattr(conversations_router.local_processing_queue, 'ensure_queued', queued)
    enrich = MagicMock(side_effect=AssertionError('first-open enrichment must not run'))
    monkeypatch.setattr(conversations_router, '_enrich_deferred_conversation', enrich)
    monkeypatch.setattr(conversations_router, 'populate_speaker_names', lambda *a, **k: None, raising=False)

    try:
        conversations_router.get_conversation_by_id(
            'c1',
            source=None,
            include_discarded=None,
            uid='u1',
            include_translations=False,
            translation_cursor=None,
            response=None,
            request=None,
        )
    except AssertionError:
        raise
    except Exception:
        # Later presentation steps need more fakes; the decision under test is already made.
        pass
    queued.assert_called_once_with('u1', 'c1')
    enrich.assert_not_called()
