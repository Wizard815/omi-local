"""database.vector_db routes to the local Chroma store when LOCAL_VECTOR_DB=chroma.

The chroma functions used to be imported inside the `if _use_chroma:` block at
module load, so every call site read a possibly-unbound name (pyright rejected
the module and the Pinecone path could not even reference them). They are now
reached through one lazy accessor; this pins that the accessor is what the
write, batch write, search and delete paths call, with the id scheme Chroma
rows carry ('{uid}-{conversation_id}').
"""

import database.chroma_vector as chroma_vector
import database.vector_db as vector_db


def _route_to_chroma(monkeypatch):
    calls = []
    monkeypatch.setattr(vector_db, '_use_chroma', True)
    monkeypatch.setattr(vector_db, 'index', None)
    monkeypatch.setattr(chroma_vector, 'chroma_upsert', lambda *a, **k: calls.append(('upsert', a, k)))
    monkeypatch.setattr(chroma_vector, 'chroma_upsert_batch', lambda *a, **k: calls.append(('batch', a, k)))
    monkeypatch.setattr(chroma_vector, 'chroma_delete', lambda *a, **k: calls.append(('delete', a, k)))

    def query(**k):
        calls.append(('query', (), k))
        return ['u1-c1', 'u1-c2']

    monkeypatch.setattr(chroma_vector, 'chroma_query', query)
    return calls


def test_writes_and_deletes_reach_chroma_with_the_uid_prefixed_id(monkeypatch):
    calls = _route_to_chroma(monkeypatch)
    vector_db.upsert_vector('u1', 'c1', [0.1])
    assert vector_db.upsert_vector2('u1', 'c1', [0.1], {'k': 'v'}) is True
    vector_db.upsert_vectors('u1', [[0.1], [0.2]], ['c1', 'c2'])
    vector_db.delete_vector('u1', 'c1')
    kinds = [c[0] for c in calls]
    assert kinds == ['upsert', 'upsert', 'batch', 'delete']
    assert calls[0][1][0] == 'u1-c1'
    assert calls[1][1][2]['k'] == 'v'
    assert calls[2][1][0] == ['u1-c1', 'u1-c2']
    assert calls[3][1][0] == ['u1-c1']


def test_search_goes_to_chroma_and_strips_the_uid_prefix(monkeypatch):
    calls = _route_to_chroma(monkeypatch)
    ids = vector_db.query_vectors('hello', 'u1', starts_at=5, ends_at=9, k=2, query_vector=[0.3])
    assert ids == ['c1', 'c2']
    assert calls == [
        (
            'query',
            (),
            {'query_vector': [0.3], 'uid': 'u1', 'top_k': 2, 'filter_created_after': 5, 'filter_created_before': 9},
        )
    ]


def test_action_item_vectors_use_the_local_store(monkeypatch, tmp_path):
    """Without Pinecone the task duplicate finder used to return nothing, so tasks never merged."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(chroma_vector, 'CHROMA_PATH', str(tmp_path / 'chroma'))
    monkeypatch.setattr(chroma_vector, '_chroma_client', None)
    monkeypatch.setattr(chroma_vector, '_chroma_collection', None)
    monkeypatch.setattr(chroma_vector, '_chroma_enabled', False)
    monkeypatch.setattr(vector_db, '_use_chroma', True)
    monkeypatch.setattr(vector_db, 'index', None)
    vectors = {
        'Call Gary the plumber': [1.0, 0.0, 0.0],
        'Call the plumber Gary back': [0.98, 0.2, 0.0],
        'Buy salmon cat food': [0.0, 1.0, 0.0],
    }
    fake = MagicMock()
    fake.embed_query.side_effect = lambda text: vectors[text]
    fake.embed_documents.side_effect = lambda texts: [vectors[t] for t in texts]
    monkeypatch.setattr(vector_db, 'embeddings', fake)

    stored = vector_db.upsert_action_item_vectors_batch(
        'u1',
        [
            {'action_item_id': 'plumber', 'description': 'Call Gary the plumber'},
            {'action_item_id': 'cat', 'description': 'Buy salmon cat food'},
        ],
    )
    assert stored == 2

    similar = vector_db.find_similar_action_items('u1', 'Call the plumber Gary back', threshold=0.6)
    assert [m['action_item_id'] for m in similar] == ['plumber']
    assert vector_db.search_action_items_by_vector('u1', 'Call Gary the plumber', limit=1) == ['plumber']
    assert vector_db.find_similar_action_items('someone-else', 'Call Gary the plumber') == []

    vector_db.delete_action_item_vectors_batch('u1', ['plumber'])
    assert vector_db.find_similar_action_items('u1', 'Call Gary the plumber', threshold=0.6) == []
