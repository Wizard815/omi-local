"""users/{uid}/integrations/approval: the user's auto-approve choice (omi-local).

Off unless the user turns it on. When on, at ``time`` (the user's local HH:MM) the
waiting tasks, the waiting calendar events and the day summary drafts of past days are
approved as if the user had tapped Approve (utils/auto_approve.py). ``last_run_date``
is the local date the job last ran for, so it runs once a day."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from ._client import db

COLLECTION = 'integrations'
DOCUMENT = 'approval'

DEFAULTS: Dict[str, Any] = {
    'auto_approve': False,
    'time': '21:00',
    'last_run_date': '',
}


def _ref(uid: str):
    return db.collection('users').document(uid).collection(COLLECTION).document(DOCUMENT)


def get_settings(uid: str) -> Dict[str, Any]:
    snapshot = _ref(uid).get()
    if not getattr(snapshot, 'exists', False):
        return dict(DEFAULTS)
    return {**DEFAULTS, **(snapshot.to_dict() or {})}


def set_settings(uid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    update = {k: v for k, v in data.items() if k in DEFAULTS and v is not None}
    update['updated_at'] = datetime.now(timezone.utc)
    _ref(uid).set(update, merge=True)
    return get_settings(uid)


def record_run(uid: str, local_date: str) -> None:
    _ref(uid).set({'last_run_date': local_date, 'last_run_at': datetime.now(timezone.utc)}, merge=True)
