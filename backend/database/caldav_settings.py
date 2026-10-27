"""users/{uid}/integrations/caldav: where this user's CalDAV server is and what
to sync. The password is stored encrypted with the user's derived key
(utils.encryption) and never leaves the backend; the API returns only
``has_password``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils import encryption

from ._client import db

COLLECTION = 'integrations'
DOCUMENT = 'caldav'

DEFAULTS: Dict[str, Any] = {
    'url': '',
    'username': '',
    'calendar_href': '',
    'tasks_href': '',
    'push_tasks': True,
    'pull_tasks': True,
    'push_day_summaries': True,
    'push_conversation_events': True,
    'push_conversations': False,
    'pull_events': True,
    'enabled': True,
}


def _ref(uid: str):
    return db.collection('users').document(uid).collection(COLLECTION).document(DOCUMENT)


def get_settings(uid: str) -> Optional[Dict[str, Any]]:
    snapshot = _ref(uid).get()
    if not getattr(snapshot, 'exists', False):
        return None
    return {**DEFAULTS, **(snapshot.to_dict() or {})}


def set_settings(uid: str, data: Dict[str, Any], *, password: Optional[str] = None) -> Dict[str, Any]:
    """Merge ``data`` over the stored settings; ``password`` (plain) replaces the stored one when given."""
    current = get_settings(uid) or dict(DEFAULTS)
    merged = {**current, **{k: v for k, v in data.items() if k in DEFAULTS}}
    if password is not None:
        merged['password_enc'] = encryption.encrypt(password, uid) if password else ''
    merged['updated_at'] = datetime.now(timezone.utc)
    _ref(uid).set(merged)
    return merged


def get_password(uid: str, settings: Dict[str, Any]) -> str:
    token = settings.get('password_enc') or ''
    if not token:
        return ''
    try:
        return encryption.decrypt(token, uid)
    except Exception:  # noqa: BLE001 — a key rotation leaves the password unreadable: treat as unset
        return ''


def record_sync(uid: str, *, error: Optional[str], report: Optional[Dict[str, Any]] = None) -> None:
    _ref(uid).set(
        {
            'last_sync_at': datetime.now(timezone.utc),
            'last_error': error or '',
            'last_report': report or {},
        },
        merge=True,
    )


def delete_settings(uid: str) -> None:
    _ref(uid).delete()
