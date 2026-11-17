"""users/{uid}/integrations/outbound: where Omi may send this user's things (omi-local).

One map per target (wiki, hermes, odysseus, studio, coinpile, orb). Every target is off
until the user turns it on; nothing is sent from here on its own (utils/integrations_out.py
decides what may leave and when). Tokens are stored encrypted with the user's derived key
(utils.encryption) under ``token_enc`` and are never returned: the API answers ``has_token``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils import encryption

from ._client import get_firestore_client

COLLECTION = 'integrations'
DOCUMENT = 'outbound'

TARGETS = ('wiki', 'hermes', 'odysseus', 'studio', 'coinpile', 'orb')

# Per target: the user's choices (no secrets) and their defaults.
DEFAULTS: Dict[str, Dict[str, Any]] = {
    'wiki': {
        'enabled': False,
        'url': '',
        'person': '',
        'send_day_reviews': True,
        'send_kept_conversations': 'off',  # off | ask | on_keep
        'include_transcript': False,
        'require_manual_approval': True,
    },
    'hermes': {'enabled': False, 'url': ''},
    'odysseus': {'enabled': False, 'url': '', 'endpoint_id': '', 'model': '', 'session_id': ''},
    'studio': {'enabled': False, 'url': '', 'default_agent': 'ekko-agent', 'workspace': ''},
    'coinpile': {'enabled': False, 'url': ''},
    'orb': {
        'enabled': False,
        'url': '',
        'log_day_approvals': False,
        'project': 'General',
        'require_manual_approval': True,
    },
}
# Secrets a target may hold, stored as '<name>_enc'.
SECRETS: Dict[str, tuple] = {
    'wiki': ('token',),
    'hermes': ('token',),
    'odysseus': ('token',),
    'studio': ('token', 'webhook_secret'),
    'coinpile': ('token',),
    'orb': ('token',),
}


def _ref(uid: str, *, firestore_client: Any = None):
    client = firestore_client or get_firestore_client()
    return client.collection('users').document(uid).collection(COLLECTION).document(DOCUMENT)


def get_raw(uid: str, *, firestore_client: Any = None) -> Dict[str, Dict[str, Any]]:
    """Every target's stored map over its defaults (secrets still encrypted)."""
    snapshot = _ref(uid, firestore_client=firestore_client).get()
    stored = (snapshot.to_dict() or {}) if getattr(snapshot, 'exists', False) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for target in TARGETS:
        row = stored.get(target) if isinstance(stored.get(target), dict) else {}
        out[target] = {**DEFAULTS[target], **(row or {})}
    return out


def set_target(
    uid: str,
    target: str,
    data: Dict[str, Any],
    *,
    secrets: Optional[Dict[str, Optional[str]]] = None,
    firestore_client: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """Merge the user's choices for one target; a secret given as '' clears it, None keeps it."""
    if target not in TARGETS:
        raise ValueError(f'unknown target {target}')
    current = get_raw(uid, firestore_client=firestore_client)[target]
    merged = {**current, **{k: v for k, v in data.items() if k in DEFAULTS[target] and v is not None}}
    for name, value in (secrets or {}).items():
        if name not in SECRETS[target] or value is None:
            continue
        merged[f'{name}_enc'] = encryption.encrypt(value, uid) if value else ''
    merged['updated_at'] = datetime.now(timezone.utc)
    _ref(uid, firestore_client=firestore_client).set({target: merged}, merge=True)
    return get_raw(uid, firestore_client=firestore_client)


def secret(uid: str, row: Dict[str, Any], name: str = 'token') -> str:
    """A target's stored secret in plain text ('' when unset or unreadable after a key change)."""
    token = row.get(f'{name}_enc') or ''
    if not token:
        return ''
    try:
        return encryption.decrypt(token, uid)
    except Exception:  # noqa: BLE001 — unreadable after a key rotation: treat as unset
        return ''


def record_check(uid: str, target: str, *, ok: bool, error: str = '', firestore_client: Any = None) -> None:
    """The last connection test (Test connection in the app), so the page can show it."""
    _ref(uid, firestore_client=firestore_client).set(
        {target: {'last_check_at': datetime.now(timezone.utc), 'last_check_ok': ok, 'last_check_error': error[:300]}},
        merge=True,
    )
