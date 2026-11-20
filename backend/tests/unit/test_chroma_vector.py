"""database/chroma_vector.py: the self-hosted vector store stores and finds vectors.

Two defects, both silent:
- every function returned on `not _chroma_enabled` before calling
  _init_chroma(), the only thing that sets it, so with LOCAL_VECTOR_DB=chroma the
  store never opened: nothing was stored and every search came back empty;
- a uid plus a date range built {"uid": ..., "$and": [...]}, which Chroma
  rejects, so those searches errored and returned [].

Seam: a real chromadb PersistentClient in a tmp dir.
"""

import pytest

import database.chroma_vector as chroma_vector


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(chroma_vector, 'CHROMA_PATH', str(tmp_path / 'chroma'))
    monkeypatch.setattr(chroma_vector, '_chroma_client', None)
    monkeypatch.setattr(chroma_vector, '_chroma_collection', None)
    monkeypatch.setattr(chroma_vector, '_chroma_enabled', False)
    return chroma_vector


def test_first_write_opens_the_store_and_a_search_finds_it(store):
    store.chroma_upsert('uid-a-c1', [1.0, 0.0, 0.0], {'uid': 'uid-a', 'created_at': 100})
    store.chroma_upsert_batch(
        ['uid-a-c2', 'uid-b-c3'],
        [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]],
        [{'uid': 'uid-a', 'created_at': 200}, {'uid': 'uid-b', 'created_at': 100}],
    )

    assert store.chroma_count() == 3
    assert store.chroma_query(query_vector=[1.0, 0.0, 0.0], uid='uid-a', top_k=1) == ['uid-a-c1']


def test_uid_and_date_range_filters_combine(store):
    store.chroma_upsert_batch(
        ['uid-a-c1', 'uid-a-c2', 'uid-a-c3', 'uid-b-c4'],
        [[1.0, 0.0, 0.0]] * 4,
        [
            {'uid': 'uid-a', 'created_at': 100},
            {'uid': 'uid-a', 'created_at': 200},
            {'uid': 'uid-a', 'created_at': 300},
            {'uid': 'uid-b', 'created_at': 200},
        ],
    )

    ids = store.chroma_query(
        query_vector=[1.0, 0.0, 0.0], uid='uid-a', top_k=10, filter_created_after=150, filter_created_before=250
    )

    assert ids == ['uid-a-c2']


def test_delete_by_uid_leaves_other_users(store):
    store.chroma_upsert('uid-a-c1', [1.0, 0.0, 0.0], {'uid': 'uid-a', 'created_at': 100})
    store.chroma_upsert('uid-b-c2', [1.0, 0.0, 0.0], {'uid': 'uid-b', 'created_at': 100})

    store.chroma_delete_by_uid('uid-a')

    assert store.chroma_count() == 1
    assert store.chroma_query(query_vector=[1.0, 0.0, 0.0], uid='uid-b') == ['uid-b-c2']


def test_empty_list_and_none_metadata_do_not_drop_the_record(store):
    """A conversation whose dates came back empty was rejected by Chroma and never searchable."""
    store.chroma_upsert(
        'uid-a-c1',
        [1.0, 0.0, 0.0],
        {'uid': 'uid-a', 'created_at': 100, 'dates': [], 'people': ['Gary'], 'overview': None},
    )
    store.chroma_upsert_batch(
        ['uid-a-c2'], [[0.0, 1.0, 0.0]], [{'uid': 'uid-a', 'created_at': 200, 'topics': [], 'dates': None}]
    )

    assert store.chroma_count() == 2
    assert store.chroma_query(query_vector=[1.0, 0.0, 0.0], uid='uid-a', top_k=1) == ['uid-a-c1']


def test_action_items_live_apart_and_report_cosine_similarity(store):
    """Self-hosted task dedup: similar tasks are found, scored like Pinecone, scoped per user."""
    assert (
        store.chroma_upsert_action_items(
            ['uid-a-ai-t1', 'uid-a-ai-t2', 'uid-b-ai-t3'],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]],
            [
                {'uid': 'uid-a', 'action_item_id': 't1', 'created_at': 1},
                {'uid': 'uid-a', 'action_item_id': 't2', 'created_at': 1},
                {'uid': 'uid-b', 'action_item_id': 't3', 'created_at': 1},
            ],
        )
        == 3
    )
    # Conversation search never returns a task.
    assert store.chroma_count() == 0

    matches = store.chroma_query_action_items([0.9, 0.1, 0.0], 'uid-a', 5)
    assert [m['metadata']['action_item_id'] for m in matches] == ['t1', 't2']
    assert matches[0]['score'] > 0.95 > matches[1]['score']

    store.chroma_delete_action_items(['uid-a-ai-t1'])
    assert [m['id'] for m in store.chroma_query_action_items([1.0, 0.0, 0.0], 'uid-a', 5)] == ['uid-a-ai-t2']
    store.chroma_delete_by_uid('uid-b')
    assert store.chroma_query_action_items([1.0, 0.0, 0.0], 'uid-b', 5) == []
