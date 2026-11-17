"""users/{uid}/agent_jobs/{job_id}: what the user sent to an agent and what came back (omi-local).

One document per "Send to agent": the target (odysseus, hermes, studio), the exact prompt and
context that left Omi, the agent's id for it (Hermes run, Studio chat, Odysseus chat), its
status, a pending approval, and the result. utils/agent_jobs.py owns the transitions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from ._client import get_firestore_client

COLLECTION = 'agent_jobs'


def _col(uid: str, *, firestore_client: Any = None):
    client = firestore_client or get_firestore_client()
    return client.collection('users').document(uid).collection(COLLECTION)


def create(uid: str, job_id: str, data: Dict[str, Any], *, firestore_client: Any = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    row = {**data, 'id': job_id, 'created_at': now, 'updated_at': now}
    _col(uid, firestore_client=firestore_client).document(job_id).set(row)
    return row


def get(uid: str, job_id: str, *, firestore_client: Any = None) -> Optional[Dict[str, Any]]:
    snapshot = _col(uid, firestore_client=firestore_client).document(job_id).get()
    if not getattr(snapshot, 'exists', False):
        return None
    return {**(snapshot.to_dict() or {}), 'id': job_id}


def update(uid: str, job_id: str, fields: Dict[str, Any], *, firestore_client: Any = None) -> None:
    _col(uid, firestore_client=firestore_client).document(job_id).set(
        {**fields, 'updated_at': datetime.now(timezone.utc)}, merge=True
    )


def list_recent(uid: str, limit: int = 50, *, firestore_client: Any = None) -> List[Dict[str, Any]]:
    query = (
        _col(uid, firestore_client=firestore_client)
        .order_by('created_at', direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    return [{**(doc.to_dict() or {}), 'id': doc.id} for doc in query.stream()]
