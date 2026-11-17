"""users/{uid}/outbound_receipts/{key}: what Omi sent where for this user (omi-local).

``key`` names the thing: ``wiki_day_<date>``, ``wiki_conversation_<id>``, ``coinpile_<id>``,
``orb_day_<date>``. Each document keeps every copy sent (``receipts``, oldest first), so a
second send is filed as a revision and the app can say "sent on ...". Receipts are a record,
not a queue: nothing reads them to decide work except "was this sent already".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from ._client import get_firestore_client

COLLECTION = 'outbound_receipts'
MAX_KEPT = 50


def _ref(uid: str, key: str, *, firestore_client: Any = None):
    client = firestore_client or get_firestore_client()
    return client.collection('users').document(uid).collection(COLLECTION).document(key.replace('/', '_'))


def receipts(uid: str, key: str, *, firestore_client: Any = None) -> List[Dict[str, Any]]:
    snapshot = _ref(uid, key, firestore_client=firestore_client).get()
    if not getattr(snapshot, 'exists', False):
        return []
    return list((snapshot.to_dict() or {}).get('receipts') or [])


def add(uid: str, key: str, receipt: Dict[str, Any], *, firestore_client: Any = None) -> List[Dict[str, Any]]:
    rows = [*receipts(uid, key, firestore_client=firestore_client), receipt][-MAX_KEPT:]
    _ref(uid, key, firestore_client=firestore_client).set({'receipts': rows, 'updated_at': datetime.now(timezone.utc)})
    return rows
