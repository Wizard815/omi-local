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
