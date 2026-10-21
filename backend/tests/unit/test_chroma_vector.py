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
