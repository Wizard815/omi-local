"""omi-local: /v1/conversations/search without a Typesense server.

The self-hosted stack has no Typesense; every keyword search raised (no host) and the
route answered 500. With no server configured, search reads the user's conversations from
Firestore in one bounded pass and matches their text instead.
"""

from datetime import datetime, timezone

import pytest

from utils.conversations import search

ROWS = [
    {
        'id': 'new-unrelated',
        'structured': {'title': 'Breakfast', 'overview': 'Pancakes and eggs'},
        'transcript_segments': [{'text': 'more syrup please', 'is_user': True}],
    },
    {
        'id': 'transcript-only',
        'structured': {'title': 'Evening chores', 'overview': 'Dishes'},
        'transcript_segments': [{'text': 'remember Gary the plumber comes Thursday', 'is_user': True}],
    },
    {
        'id': 'title-hit',
        'structured': {'title': 'Call with the plumber', 'overview': 'Gary fixes the sink Thursday'},
        'transcript_segments': [],
    },
    {
        'id': 'locked',
        'is_locked': True,
        'structured': {'title': 'Plumber invoice', 'overview': 'Gary'},
    },
]


def test_matches_title_before_transcript_and_skips_unrelated_and_locked():
    page = search.local_text_search(iter(ROWS), 'What did Gary the plumber say?', page=1, per_page=10)
    assert [r['id'] for r in page['items']] == ['title-hit', 'transcript-only']
    assert page['total_pages'] == 1
    with_locked = search.local_text_search(iter(ROWS), 'plumber', page=1, per_page=10, include_locked=True)
    assert 'locked' in [r['id'] for r in with_locked['items']]


def test_paging_and_filter_only_browse():
    first = search.local_text_search(iter(ROWS), 'plumber', page=1, per_page=1)
    assert [r['id'] for r in first['items']] == ['title-hit'] and first['total_pages'] == 2
    browse = search.local_text_search(iter(ROWS), '', page=1, per_page=2)
    assert [r['id'] for r in browse['items']] == ['new-unrelated', 'transcript-only']


def test_search_conversations_uses_the_local_scan_without_typesense(monkeypatch):
    monkeypatch.delenv('TYPESENSE_HOST', raising=False)
    monkeypatch.delenv('TYPESENSE_API_KEY', raising=False)
    seen = {}

    def fake_iter(uid, **kwargs):
        seen.update(kwargs, uid=uid)
        return iter(ROWS)

    monkeypatch.setattr(search, 'iter_conversations', fake_iter)
    monkeypatch.setattr(search, 'conversation_scan_budget', lambda request, route: 'budget')
    result = search.search_conversations('u1', 'plumber', page=1, per_page=10, start_date=1763600000)
    assert [r['id'] for r in result['items']] == ['title-hit', 'transcript-only']
    assert seen['uid'] == 'u1' and seen['budget'] == 'budget'
    assert seen['start_date'] == datetime.fromtimestamp(1763600000, tz=timezone.utc)
    assert search.keyword_search_conversation_ids('u1', 'plumber') == ['title-hit', 'transcript-only']


def test_a_configured_server_is_still_used(monkeypatch):
    monkeypatch.setenv('TYPESENSE_HOST', 'typesense')
    monkeypatch.setenv('TYPESENSE_API_KEY', 'k')
    assert search.typesense_configured()
    calls = []

    def typesense():
        calls.append('typesense')
        raise RuntimeError('no network in tests')

    monkeypatch.setattr(search, 'iter_conversations', lambda *a, **k: calls.append('firestore'))
    monkeypatch.setattr(search, '_get_typesense_client', typesense)
    with pytest.raises(Exception, match='Failed to search'):
        search.search_conversations('u1', 'plumber')
    assert calls == ['typesense']
