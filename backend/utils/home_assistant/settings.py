"""Home Assistant settings (omi-local): where Home Assistant is, and what Omi may do there.

The connection is the stack's (OMI_HA_URL + OMI_HA_TOKEN, a long-lived access token) unless a
user stored their own address and token (PUT /v1/home-assistant/settings), the same way a
CalDAV account is the bundled one unless the user set their own. The token stays on the
server: the API answers ``has_token`` only.

What the agent may touch is the operator's, from env, and applies to every connection:

- OMI_HA_READ_ALLOW: entity ids or domains the agent may read (default DEFAULT_READ; ``*``
  allows every entity). ha_whos_home reads person.* (device_tracker.* when there is no
  person) and needs ``person`` or ``device_tracker`` here.
- OMI_HA_ACTION_DOMAINS: domains ha_call_service may call (default DEFAULT_ACTION_DOMAINS).
  SENSITIVE (lock, alarm_control_panel, cover, and anything named garage) is never callable
  unless listed here by name, and then every call still waits for the user's OK.
  FORBIDDEN domains (Home Assistant's own administration) are never callable.
- OMI_HA_AUTO_APPROVE: entity ids or domains whose calls run without waiting for the user's
  OK. Sensitive entities are never auto-approved, whatever this says.
- OMI_HA_EVENTS: the event types fired for users who have not chosen (default none).
- OMI_HA_TIMEOUT_SECONDS: per request (default 5; Home Assistant is on the LAN).

Home Assistant is a LAN address: it must be listed in OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS
(utils/outbound_http.py), and omi-host's egress guard lets LAN addresses through.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence

import database.home_assistant as store

URL_ENV = 'OMI_HA_URL'
TOKEN_ENV = 'OMI_HA_TOKEN'
READ_ENV = 'OMI_HA_READ_ALLOW'
ACTION_ENV = 'OMI_HA_ACTION_DOMAINS'
AUTO_ENV = 'OMI_HA_AUTO_APPROVE'
EVENTS_ENV = 'OMI_HA_EVENTS'
TIMEOUT_ENV = 'OMI_HA_TIMEOUT_SECONDS'

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ = ('light', 'switch', 'climate', 'media_player', 'scene', 'person')
DEFAULT_ACTION_DOMAINS = ('light', 'switch', 'scene', 'script', 'media_player', 'climate')
SENSITIVE_DOMAINS = frozenset({'lock', 'alarm_control_panel', 'cover', 'garage'})
SENSITIVE_WORD = 'garage'
FORBIDDEN_DOMAINS = frozenset(
    {
        'homeassistant',
        'hassio',
        'backup',
        'update',
        'recorder',
        'shell_command',
        'command_line',
        'python_script',
        'pyscript',
        'rest_command',
    }
)
# A waiting action can be approved this long after it was asked for.
APPROVAL_TTL = timedelta(hours=1)
EVENT_TYPES = ('conversation_finished', 'task_created', 'task_due', 'waiting_for_approval', 'day_summary_ready')
EVENT_DETAILS = ('summary', 'minimal')

_ENTITY = re.compile(r'^[a-z_][a-z0-9_]*\.[a-z0-9_]+$')
_DOMAIN = re.compile(r'^[a-z_][a-z0-9_]*$')


def _list(name: str, default: Sequence[str] = ()) -> List[str]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)
    return [part.strip().lower() for part in raw.split(',') if part.strip()]


def timeout() -> float:
    raw = os.getenv(TIMEOUT_ENV, '').strip()
    try:
        return max(0.5, min(30.0, float(raw))) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def is_entity_id(value: str) -> bool:
    return bool(_ENTITY.match(value or ''))


def domain_of(entity_id: str) -> str:
    return (entity_id or '').split('.', 1)[0]


def _matches(entity_id: str, allowed: Iterable[str]) -> bool:
    entries = set(allowed)
    return '*' in entries or entity_id in entries or domain_of(entity_id) in entries


def read_allow() -> List[str]:
    return _list(READ_ENV, DEFAULT_READ)


def may_read(entity_id: str, allowed: Optional[Iterable[str]] = None) -> bool:
    return _matches(entity_id, read_allow() if allowed is None else allowed)


def action_domains() -> FrozenSet[str]:
    return frozenset(d for d in _list(ACTION_ENV, DEFAULT_ACTION_DOMAINS) if _DOMAIN.match(d))


def auto_approve() -> List[str]:
    return _list(AUTO_ENV)


def is_sensitive(entity_id: str) -> bool:
    return domain_of(entity_id) in SENSITIVE_DOMAINS or SENSITIVE_WORD in entity_id


def sensitive_enabled(entity_id: str, domains: Optional[FrozenSet[str]] = None) -> bool:
    """The operator named this sensitive kind in OMI_HA_ACTION_DOMAINS (lock, cover, garage...)."""
    listed = action_domains() if domains is None else domains
    if SENSITIVE_WORD in entity_id and SENSITIVE_WORD not in listed:
        return False
    return domain_of(entity_id) not in SENSITIVE_DOMAINS or domain_of(entity_id) in listed


def auto_approved(entity_id: str, auto: Optional[Iterable[str]] = None) -> bool:
    if is_sensitive(entity_id):
        return False
    entries = set(auto_approve() if auto is None else auto) - {'*'}
    return entity_id in entries or domain_of(entity_id) in entries


def expired(row: Dict[str, Any], now: datetime) -> bool:
    """A waiting action asked for longer ago than APPROVAL_TTL."""
    created = row.get('created_at')
    if not isinstance(created, datetime):
        return False
    created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    return now - created > APPROVAL_TTL


def env_events() -> List[str]:
    return [e for e in _list(EVENTS_ENV) if e in EVENT_TYPES]


def effective(uid: str) -> Dict[str, Any]:
    """The connection and event choices a sender uses: the user's own address and token, else
    the stack's. Contains the token in plain text: never return this to a client."""
    raw = store.get_raw(uid)
    row = dict(raw or store.DEFAULTS)
    own_url = str(row.get('url') or '').strip().rstrip('/')
    own_token = store.get_token(uid, raw)
    env_url = os.getenv(URL_ENV, '').strip().rstrip('/')
    env_token = os.getenv(TOKEN_ENV, '').strip()
    defaults = env_events()
    chosen = row.get('events') or {}
    return {
        'stored': raw is not None,
        'enabled': bool(row.get('enabled', True)),
        'url': own_url or env_url,
        'url_from_env': not own_url and bool(env_url),
        'token': own_token or (env_token if not own_url or own_url == env_url else ''),
        'token_from_env': not own_token and bool(env_token) and (not own_url or own_url == env_url),
        'person': str(row.get('person') or '').strip()[:60],
        'events': {e: bool(chosen[e]) if e in chosen else e in defaults for e in EVENT_TYPES},
        'event_detail': row.get('event_detail') if row.get('event_detail') in EVENT_DETAILS else 'summary',
        'waiting_count': int(row.get('waiting_count') or 0),
        'due_checked_at': row.get('due_checked_at'),
        'last_check_at': row.get('last_check_at'),
        'last_check_ok': row.get('last_check_ok'),
        'last_check_error': str(row.get('last_check_error') or ''),
    }


def ready(settings: Dict[str, Any]) -> bool:
    return bool(settings.get('enabled')) and bool(settings.get('url')) and bool(settings.get('token'))


def present(settings: Dict[str, Any]) -> Dict[str, Any]:
    """What the app may see: no token, only whether one is set."""
    return {
        'configured': bool(settings.get('url')) and bool(settings.get('token')),
        'enabled': bool(settings.get('enabled')),
        'url': settings.get('url') or '',
        'url_from_env': bool(settings.get('url_from_env')),
        'has_token': bool(settings.get('token')),
        'token_from_env': bool(settings.get('token_from_env')),
        'person': settings.get('person') or '',
        'events': dict(settings.get('events') or {}),
        'event_detail': settings.get('event_detail') or 'summary',
        'read_allow': read_allow(),
        'action_domains': sorted(action_domains()),
        'auto_approve': auto_approve(),
        'last_check_at': settings.get('last_check_at'),
        'last_check_ok': settings.get('last_check_ok'),
        'last_check_error': settings.get('last_check_error') or '',
    }


def clean_url(value: str) -> str:
    url = str(value or '').strip().rstrip('/')
    if url and not url.startswith(('http://', 'https://')):
        raise ValueError('The address must start with http:// or https://')
    return url
