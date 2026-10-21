"""users/{uid}/day_reviews/{YYYY-MM-DD}: one end-of-day review per local date."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from google.cloud import firestore

from ._client import db

COLLECTION = 'day_reviews'


def _ref(uid: str, date: str):
    return db.collection('users').document(uid).collection(COLLECTION).document(date)


def get_day_review(uid: str, date: str) -> Optional[Dict[str, Any]]:
    snapshot = _ref(uid, date).get()
    if not getattr(snapshot, 'exists', False):
        return None
    data = snapshot.to_dict() or {}
    data['date'] = date
    return data


def set_day_review(uid: str, date: str, data: Dict[str, Any]) -> None:
    _ref(uid, date).set({**data, 'date': date})


def delete_day_review(uid: str, date: str) -> None:
    _ref(uid, date).delete()


def list_day_reviews(uid: str, limit: int = 30) -> List[Dict[str, Any]]:
    query = (
        db.collection('users')
        .document(uid)
        .collection(COLLECTION)
        .order_by('date', direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    out: List[Dict[str, Any]] = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        data['date'] = doc.id
        out.append(data)
    return out
