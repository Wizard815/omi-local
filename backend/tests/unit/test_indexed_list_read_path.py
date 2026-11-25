"""Offline list-read economics, visibility equivalence, and deadline regressions."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from google.api_core.exceptions import DeadlineExceeded
from google.cloud.firestore_v1 import And, Or

from database.firestore_retry import FIRESTORE_NO_RETRY
from utils.other.list_budget import ListReadBudget, ListReadBudgetExhausted, budgeted_stream_list

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def _matches(payload, predicate):
    if isinstance(predicate, (And, Or)):
        results = [_matches(payload, child) for child in predicate.filters]
        return all(results) if isinstance(predicate, And) else any(results)
    value = payload
    for name in predicate.field_path.split('.'):
        if not isinstance(value, dict) or name not in value:
            return False
        value = value[name]
    if predicate.op_string == 'in':
        return value in predicate.value
    assert predicate.op_string == '=='
    return type(value) is type(predicate.value) and value == predicate.value


@dataclass
class Query:
    db: Any
    filters: tuple = ()
    maximum: int = 100
    after: Any = None
    fields: Any = None

    def document(self, doc_id):
        return SimpleNamespace(id=doc_id, collection=lambda name: self)

    def collection(self, name):
        return self

    def where(self, *, filter):
        return replace(self, filters=(*self.filters, filter))

    def order_by(self, field, direction=None):
        return self

    def start_after(self, cursor):
        return replace(self, after=cursor)

    def offset(self, offset):
        assert offset == 0
        return self

    def limit(self, count):
        return replace(self, maximum=count)

    def select(self, fields):
        return replace(self, fields=set(fields))

    def stream(self, **kwargs):
        if kwargs:
            assert kwargs['retry'] is FIRESTORE_NO_RETRY
        rows = [row for row in self.db.rows if all(_matches(row, f) for f in self.filters)]
        rows.sort(key=lambda row: (-row['updated_at'].timestamp(), row['memory_id']))
        if self.after:
            key = (-self.after['updated_at'].timestamp(), self.after['__name__'].id)
            rows = [row for row in rows if (-row['updated_at'].timestamp(), row['memory_id']) > key]
        for row in rows[: self.maximum]:
            self.db.reads += 1
            payload = dict(row) if self.fields is None else {k: v for k, v in row.items() if k in self.fields}
            yield SimpleNamespace(id=row['memory_id'], to_dict=lambda payload=payload: dict(payload))


class Db:
    def __init__(self, rows):
        self.rows = rows
        self.reads = 0

    def collection(self, path):
        return Query(self)


def _row(memory_id, *, processing='processed', status='active', required=False, **overrides) -> dict[str, Any]:
    return dict(
        memory_id=memory_id,
        uid='list-user',
        version=1,
        tier='short_term',
        status=status,
        processing_state=processing,
        content=memory_id,
        source_state='active',
        sensitivity_labels=[],
        visibility='private',
        user_asserted=True,
        captured_at=NOW - timedelta(days=40),
        updated_at=NOW,
        expires_at=NOW - timedelta(days=10),
        promotion={'required': required},
        **overrides,
    )


def test_budgeted_stream_keeps_downloaded_prefix_and_disables_sdk_retries():
    closed = []

    class SlowQuery:
        def stream(self, *, timeout, retry):
            assert timeout == 5
            assert retry is FIRESTORE_NO_RETRY
            try:
                yield 'first'
                yield 'second'
                raise DeadlineExceeded('slow provider')
            finally:
                closed.append(True)

    budget = ListReadBudget(deadline_monotonic=5, clock=lambda: 0)
    with pytest.raises(ListReadBudgetExhausted) as failure:
        budgeted_stream_list(SlowQuery(), budget)
    assert getattr(failure.value, 'partial_snapshots') == ['first', 'second']
    assert budget.docs_scanned == 2
    assert budget.truncated
    assert closed == [True]


def test_canonical_scan_preserves_verified_prefix_after_provider_deadline(monkeypatch):
    from utils.memory import canonical_memory_adapter as adapter

    real_stream = Query.stream

    def interrupted(self, **kwargs):
        for doc in real_stream(self, **kwargs):
            yield doc
            raise DeadlineExceeded('stopped after a valid row')

    monkeypatch.setattr(Query, 'stream', interrupted)
    budget = ListReadBudget(deadline_monotonic=5, clock=lambda: 0)
    slots, exhausted = adapter.read_canonical_scan_page(
        'list-user',
        db_client=Db([_row('visible')]),
        budget=budget,
        include_pending_processing=True,
    )
    assert [row.id for row, _ in slots if row is not None] == ['visible']
    assert not exhausted
    assert budget.truncated
