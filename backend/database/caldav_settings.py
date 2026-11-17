"""users/{uid}/integrations/caldav: where this user's CalDAV server is and what
to sync. The password is stored encrypted with the user's derived key
(utils.encryption) and never leaves the backend; the API returns only
``has_password``.

On the stack's bundled server (no ``url`` stored) the document still holds the
user's own choices (USER_CHOICES): which calendar and task list are theirs on
the shared CalDAV account, the task mode, and what to sync."""

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
    # 'list': the tasks go to tasks_href as top-level to-dos; 'parent': they are subtasks of one
    # "<name>'s tasks" to-do in tasks_href (a list shared with other people on the account).
    'task_mode': 'list',
}

# What a user picks even on the bundled server (everything but the server and the login).
USER_CHOICES = tuple(k for k in DEFAULTS if k not in ('url', 'username'))


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


def set_collections(uid: str, *, calendar_href: str, tasks_href: str) -> None:
    """Remember the collections the sync resolved (created or migrated), so the app shows
    them and the next sync goes straight there."""
    _ref(uid).set({'calendar_href': calendar_href, 'tasks_href': tasks_href}, merge=True)


def delete_settings(uid: str) -> None:
    _ref(uid).delete()
