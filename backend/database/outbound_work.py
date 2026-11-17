"""outbound_work/{key}: Omi's small queue of outbound work that waits (omi-local).

One document per item, deleted when done, so the whole collection is what is still owed:

- ``wiki_day:{uid}:{date}``: an approved day summary to send to the wiki once ``due_at``
  passes (the Undo window), if the day is still approved by the same approval;
- ``wiki_conv:{uid}:{conversation_id}``: a clip the user kept, to send when it is ready;
- ``orb_day:{uid}:{date}``: the optional Pondering Orb log line for an approved day;
- ``job:{uid}:{job_id}``: an agent job whose result Omi still waits for (utils/agent_jobs.py);
- ``studio:{session_id}``: which job a Studio chat belongs to (for its signed webhook).

The scheduler tick (utils/other/local_scheduler.py) reads the collection every few seconds;
it stays small (open items of this server's users only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ._client import get_firestore_client

COLLECTION = 'outbound_work'


def _ref(key: str, *, firestore_client: Any = None):
    client = firestore_client or get_firestore_client()
    return client.collection(COLLECTION).document(key.replace('/', '_'))


def put(key: str, data: Dict[str, Any], *, firestore_client: Any = None) -> None:
    _ref(key, firestore_client=firestore_client).set({**data, 'key': key, 'updated_at': datetime.now(timezone.utc)})


def update(key: str, data: Dict[str, Any], *, firestore_client: Any = None) -> None:
    _ref(key, firestore_client=firestore_client).set({**data, 'updated_at': datetime.now(timezone.utc)}, merge=True)


def get(key: str, *, firestore_client: Any = None) -> Optional[Dict[str, Any]]:
    snapshot = _ref(key, firestore_client=firestore_client).get()
    return (snapshot.to_dict() or {}) if getattr(snapshot, 'exists', False) else None


def delete(key: str, *, firestore_client: Any = None) -> None:
    _ref(key, firestore_client=firestore_client).delete()


def all_items(*, firestore_client: Any = None) -> List[Dict[str, Any]]:
    client = firestore_client or get_firestore_client()
    return [doc.to_dict() or {} for doc in client.collection(COLLECTION).stream()]
