"""omi-local: a conversation rolled into an approved day stays readable by chat.

Approving a day discards the conversations it rolled into the summary (tagged
``day_review_date``). Chat hid every discarded conversation, so "what did I promise
Gary?" missed a promise made in one of them once the day was approved.
"""

import os

os.environ.setdefault(
    'ENCRYPTION_SECRET',
    'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv',
)

import database.notifications as notifications_db
import utils.retrieval.tool_services.conversations as conversations_service
from utils.conversations import day_rollup

REVIEWS = {
    '2026-11-16': {
        'status': 'finalized',
        'rolled_up_conversation_ids': ['gary-call'],
        'dropped_conversation_ids': ['dropped'],
    },
    '2026-11-17': {'status': 'draft', 'rolled_up_conversation_ids': ['draft-day']},
}


def _reader(calls):
    def read(uid, date):
        calls.append(date)
        return REVIEWS.get(date)

    return read


def test_only_conversations_rolled_into_an_approved_day_come_back():
    rows = [
        {'id': 'gary-call', 'discarded': True, 'day_review_date': '2026-11-16'},
        {'id': 'dropped', 'discarded': True, 'day_review_date': '2026-11-16'},
        {'id': 'draft-day', 'discarded': True, 'day_review_date': '2026-11-17'},
        {'id': 'plain-discard', 'discarded': True},
        {'id': 'visible'},
    ]
    calls = []
    rolled = day_rollup.rolled_up_ids('u1', rows, get_review=_reader(calls))
    assert rolled == {'gary-call'}
    assert sorted(calls) == ['2026-11-16', '2026-11-17']  # one read per day
    assert [r['id'] for r in rows if not day_rollup.hidden_from_chat(r, rolled)] == ['gary-call', 'visible']


def test_unreadable_review_keeps_them_hidden():
    def broken(uid, date):
        raise RuntimeError('firestore down')

    rows = [{'id': 'gary-call', 'discarded': True, 'day_review_date': '2026-11-16'}]
    assert day_rollup.rolled_up_ids('u1', rows, get_review=broken) == set()


def test_chat_search_reads_a_rolled_up_conversation(monkeypatch):
    monkeypatch.setattr(notifications_db, 'get_user_time_zone', lambda _uid: 'America/New_York')
    monkeypatch.setattr(conversations_service, 'keyword_search_conversation_ids', lambda **_kwargs: ['gary-call'])
    monkeypatch.setattr(conversations_service.vector_db, 'index', None, raising=False)
    monkeypatch.setattr(conversations_service.vector_db, 'query_vectors', lambda **_kwargs: ['gary-call'])
    monkeypatch.setattr(conversations_service, 'merge_chat_conversation_ids', lambda *_args: ['gary-call', 'dropped'])
    monkeypatch.setattr(conversations_service, 'conversation_matches_date_range', lambda *_args: True)
    monkeypatch.setattr(day_rollup.day_reviews_db, 'get_day_review', lambda uid, date: REVIEWS.get(date))
    monkeypatch.setattr(
        conversations_service.conversations_db,
        'get_conversations_by_id',
        lambda *_args, **_kwargs: [
            {
                'id': 'gary-call',
                'discarded': True,
                'day_review_date': '2026-11-16',
                'created_at': '2026-11-16T14:00:00+00:00',
                'started_at': '2026-11-16T14:00:00+00:00',
                'finished_at': '2026-11-16T14:05:00+00:00',
                'structured': {'title': 'Call with Gary the plumber', 'overview': 'I promised to clear under the sink'},
                'transcript_segments': [],
            },
            {
                'id': 'dropped',
                'discarded': True,
                'day_review_date': '2026-11-16',
                'structured': {'title': 'private dropped title'},
            },
        ],
    )

    result = conversations_service.search_conversations_text(uid='u1', query='what did I promise Gary')

    assert 'Call with Gary the plumber' in result
    assert 'private dropped' not in result
    assert '1 conversations exist here but were not read: discarded' in result
