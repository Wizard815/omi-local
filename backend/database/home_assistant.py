"""Home Assistant (omi-local): one user's connection choices and their action log.

``users/{uid}/integrations/home_assistant`` holds the user's own choices: an optional own
Home Assistant address and long-lived access token (stored encrypted with the user's derived
key, utils.encryption, as ``token_enc``; never returned by the API), which events Omi fires
into Home Assistant for them, the name Home Assistant knows them by, and two bookkeeping
fields the event sender keeps (the last "waiting for your OK" count it announced, and when
it last looked for due tasks). Without a document the stack's env defaults apply
(utils/home_assistant/settings.py).

``users/{uid}/home_assistant_actions/{id}`` is the local log of every service call the
chat agent or an MCP agent asked for: refused, waiting for the user's OK, declined,
executed or failed, with who decided and when. utils/home_assistant/actions.py owns the
transitions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from utils import encryption

from . import _client

COLLECTION = 'integrations'
DOCUMENT = 'home_assistant'
ACTIONS = 'home_assistant_actions'

# The user's choices (no secrets) and their defaults. ``events`` maps an event type to
# on/off; a type the user never chose follows OMI_HA_EVENTS.
DEFAULTS: Dict[str, Any] = {
    'enabled': True,
    'url': '',
    'person': '',
    'events': {},
    'event_detail': 'summary',  # summary | minimal
}
STATE_FIELDS = ('waiting_count', 'due_checked_at')


def get_firestore_client() -> Any:
    return _client.get_firestore_client()


def _ref(uid: str, *, firestore_client: Any = None):
    client = firestore_client or get_firestore_client()
    return client.collection('users').document(uid).collection(COLLECTION).document(DOCUMENT)


def _actions(uid: str, *, firestore_client: Any = None):
    client = firestore_client or get_firestore_client()
    return client.collection('users').document(uid).collection(ACTIONS)


def get_raw(uid: str, *, firestore_client: Any = None) -> Optional[Dict[str, Any]]:
    """The stored document over the defaults (token still encrypted), or None when absent."""
    snapshot = _ref(uid, firestore_client=firestore_client).get()
    if not getattr(snapshot, 'exists', False):
        return None
    stored = snapshot.to_dict() or {}
    return {**DEFAULTS, **stored, 'events': dict(stored.get('events') or {})}


def set_settings(
    uid: str, data: Dict[str, Any], *, token: Optional[str] = None, firestore_client: Any = None
) -> Dict[str, Any]:
    """Merge the user's choices; ``token`` '' clears the stored token, None keeps it."""
    current = get_raw(uid, firestore_client=firestore_client) or dict(DEFAULTS)
    merged = {**current, **{k: v for k, v in data.items() if k in DEFAULTS and v is not None}}
    if isinstance(data.get('events'), dict):
        merged['events'] = {**(current.get('events') or {}), **data['events']}
    if token is not None:
        merged['token_enc'] = encryption.encrypt(token, uid) if token else ''
    merged['updated_at'] = datetime.now(timezone.utc)
    _ref(uid, firestore_client=firestore_client).set(merged)
    return merged


def get_token(uid: str, row: Optional[Dict[str, Any]]) -> str:
    """The user's own token in plain text ('' when unset or unreadable after a key change)."""
    value = (row or {}).get('token_enc') or ''
    if not value:
        return ''
    try:
        return encryption.decrypt(value, uid)
    except Exception:  # noqa: BLE001 — unreadable after a key rotation: treat as unset
        return ''


def set_state(uid: str, fields: Dict[str, Any], *, firestore_client: Any = None) -> None:
    """The event sender's bookkeeping (merge; never touches the user's choices)."""
    clean = {k: v for k, v in fields.items() if k in STATE_FIELDS}
    if clean:
        _ref(uid, firestore_client=firestore_client).set(clean, merge=True)


def record_check(uid: str, *, ok: bool, error: str = '', firestore_client: Any = None) -> None:
    """The last Test connection, so the settings page can show it."""
    _ref(uid, firestore_client=firestore_client).set(
        {'last_check_at': datetime.now(timezone.utc), 'last_check_ok': ok, 'last_check_error': error[:300]},
        merge=True,
    )


def delete_settings(uid: str, *, firestore_client: Any = None) -> None:
    _ref(uid, firestore_client=firestore_client).delete()


# ---- the action log ----------------------------------------------------------------------------


def create_action(uid: str, action_id: str, data: Dict[str, Any], *, firestore_client: Any = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    row = {**data, 'id': action_id, 'created_at': now, 'updated_at': now}
    _actions(uid, firestore_client=firestore_client).document(action_id).set(row)
    return row


def get_action(uid: str, action_id: str, *, firestore_client: Any = None) -> Optional[Dict[str, Any]]:
    snapshot = _actions(uid, firestore_client=firestore_client).document(action_id).get()
    if not getattr(snapshot, 'exists', False):
        return None
    return {**(snapshot.to_dict() or {}), 'id': action_id}


def update_action(uid: str, action_id: str, fields: Dict[str, Any], *, firestore_client: Any = None) -> None:
    _actions(uid, firestore_client=firestore_client).document(action_id).set(
        {**fields, 'updated_at': datetime.now(timezone.utc)}, merge=True
    )


def list_actions(uid: str, limit: int = 50, *, firestore_client: Any = None) -> List[Dict[str, Any]]:
    """The newest actions first."""
    query = (
        _actions(uid, firestore_client=firestore_client)
        .order_by('created_at', direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    return [{**(doc.to_dict() or {}), 'id': doc.id} for doc in query.stream()]
