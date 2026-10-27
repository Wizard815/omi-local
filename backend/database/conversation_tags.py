"""users/{uid}/conversation_tags/catalog: the user's tags with how many
conversations carry each one. Kept in step by set_conversation_tags (the only
tag writer), so the app's tag picker and the search overlay read one document
instead of scanning conversations."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from google.cloud import firestore

from ._client import db

COLLECTION = 'conversation_tags'
DOCUMENT = 'catalog'


def _ref(uid: str):
    return db.collection('users').document(uid).collection(COLLECTION).document(DOCUMENT)


def get_tag_catalog(uid: str) -> List[Dict[str, Any]]:
    """[{'tag', 'count'}] sorted by count desc then name; tags that reached zero are omitted."""
    snapshot = _ref(uid).get()
    data: Mapping[str, Any] = (snapshot.to_dict() or {}) if getattr(snapshot, 'exists', False) else {}
    counts = data.get('counts') or {}
    pairs: List[tuple[str, int]] = [
        (str(tag), int(count)) for tag, count in counts.items() if isinstance(count, (int, float)) and int(count) > 0
    ]
    pairs.sort(key=lambda pair: (-pair[1], pair[0]))
    return [{'tag': tag, 'count': count} for tag, count in pairs]


def apply_tag_delta(uid: str, added: Sequence[str], removed: Sequence[str]) -> None:
    """Increment the catalog for ``added`` and decrement it for ``removed``."""
    counts: Dict[str, Any] = {tag: firestore.Increment(1) for tag in added}
    counts.update({tag: firestore.Increment(-1) for tag in removed})
    if not counts:
        return
    # One merge-set: creates the document on first use and leaves other tags alone.
    _ref(uid).set({'counts': counts}, merge=True)
