"""Home Assistant service calls an agent asks for (omi-local), and the user's OK.

``request`` is what the chat agent's and the MCP ``ha_call_service`` tool run:

1. Omi's own rules first (``check``): the domain must be in OMI_HA_ACTION_DOMAINS and not
   one of Home Assistant's administration domains; the service must be one of the domain's
   safe services; every target must be an entity of that domain (no "all", no areas or
   devices); service data is a few scalar fields. Sensitive targets (lock,
   alarm_control_panel, cover, anything named garage) are refused unless the operator
   named that kind in OMI_HA_ACTION_DOMAINS. A refusal is logged as ``refused``.
2. Unless every target is in OMI_HA_AUTO_APPROVE (never a sensitive one), the call is
   stored as ``waiting_for_approval`` (Waiting for your OK) and nothing reaches Home
   Assistant until the user approves it (POST /v1/home-assistant/actions/{id}/approve). An
   approval re-checks the rules as they are then, and expires after APPROVAL_TTL (settings.py).
3. Otherwise it runs at once (``executed`` or ``failed``).

Every step is written to the user's action log (database/home_assistant.py) and logged
here, with the targets but never the token.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import database.home_assistant as store
import database.redis_db as redis_db
from utils.home_assistant.runner import db
from utils.observability.fallback import record_fallback
from utils.home_assistant import client
from utils.home_assistant import settings as ha_settings
from utils.home_assistant.context import connection

logger = logging.getLogger(__name__)

MAX_TARGETS = 10
MAX_DATA_FIELDS = 8
MAX_TEXT = 200
GENERIC_SERVICES = ('turn_on', 'turn_off', 'toggle')
SAFE_SERVICES: Dict[str, Tuple[str, ...]] = {
    'light': GENERIC_SERVICES,
    'switch': GENERIC_SERVICES,
    'scene': ('turn_on',),
    'script': GENERIC_SERVICES,
    'climate': ('turn_on', 'turn_off', 'set_temperature', 'set_hvac_mode', 'set_preset_mode', 'set_fan_mode'),
    'media_player': GENERIC_SERVICES
    + (
        'media_play',
        'media_pause',
        'media_stop',
        'media_play_pause',
        'media_next_track',
        'media_previous_track',
        'volume_set',
        'volume_up',
        'volume_down',
        'volume_mute',
        'play_media',
        'select_source',
    ),
    'lock': ('lock', 'unlock'),
    'alarm_control_panel': ('alarm_arm_home', 'alarm_arm_away', 'alarm_arm_night', 'alarm_disarm'),
    'cover': ('open_cover', 'close_cover', 'stop_cover', 'set_cover_position'),
}
_FIELD = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
_TARGETING_FIELDS = {'entity_id', 'area_id', 'device_id', 'floor_id', 'label_id'}

WAITING = 'waiting_for_approval'
WAITING_DIRTY_KEY = 'home_assistant:waiting_dirty'
FINAL = ('refused', 'declined', 'executed', 'failed', 'expired')


class ActionError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _targets(entity_id: Any) -> List[str]:
    values = entity_id if isinstance(entity_id, (list, tuple)) else str(entity_id or '').split(',')
    return list(dict.fromkeys(str(v).strip().lower() for v in values if str(v).strip()))


def _clean_data(data: Any) -> Dict[str, Any]:
    data = data or {}
    if not isinstance(data, dict):
        raise client.HomeAssistantError('refused', 'Service data must be an object of simple fields')
    if len(data) > MAX_DATA_FIELDS:
        raise client.HomeAssistantError('refused', f'At most {MAX_DATA_FIELDS} service data fields')
    out: Dict[str, Any] = {}
    for key, value in data.items():
        if key in _TARGETING_FIELDS or not _FIELD.match(str(key)):
            raise client.HomeAssistantError('refused', f'Service data field {key!r} is not allowed')
        if isinstance(value, str):
            value = value[:MAX_TEXT]
        elif isinstance(value, list):
            if len(value) > 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
                raise client.HomeAssistantError('refused', f'Service data field {key!r} must be a few numbers')
        elif not isinstance(value, (int, float, bool)):
            raise client.HomeAssistantError('refused', f'Service data field {key!r} must be a simple value')
        out[str(key)] = value
    return out


def check(domain: str, service: str, entity_id: Any, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Omi's rules for one call; raises HomeAssistantError('refused') with the reason. Returns
    the normalized call and whether it must wait for the user's OK."""
    domain = str(domain or '').strip().lower()
    service = str(service or '').strip().lower()
    targets = _targets(entity_id)
    allowed = ha_settings.action_domains()
    if domain in ha_settings.FORBIDDEN_DOMAINS:
        raise client.HomeAssistantError('refused', f'{domain} is never callable from Omi')
    if domain in ha_settings.SENSITIVE_DOMAINS and domain not in allowed:
        raise client.HomeAssistantError(
            'refused', f'{domain} is not allowed: locks, alarms, covers and garage doors stay off unless enabled'
        )
    if domain not in allowed:
        listed = ', '.join(sorted(allowed))
        raise client.HomeAssistantError('refused', f'{domain} is not in the allowed domains ({listed})')
    if service not in SAFE_SERVICES.get(domain, GENERIC_SERVICES):
        raise client.HomeAssistantError('refused', f'{domain}.{service} is not an allowed service')
    if not targets:
        raise client.HomeAssistantError('refused', 'Name the entity to act on (entity_id)')
    if len(targets) > MAX_TARGETS:
        raise client.HomeAssistantError('refused', f'At most {MAX_TARGETS} entities per call')
    for target in targets:
        if not ha_settings.is_entity_id(target) or ha_settings.domain_of(target) != domain:
            raise client.HomeAssistantError('refused', f'{target} is not a {domain} entity')
        if ha_settings.is_sensitive(target) and not ha_settings.sensitive_enabled(target, allowed):
            raise client.HomeAssistantError(
                'refused', f'{target} is not allowed: locks, alarms, covers and garage doors stay off unless enabled'
            )
    auto = ha_settings.auto_approve()
    sensitive = any(ha_settings.is_sensitive(t) for t in targets)
    return {
        'domain': domain,
        'service': service,
        'entity_ids': targets,
        'data': _clean_data(data),
        'sensitive': sensitive,
        'needs_approval': sensitive or not all(ha_settings.auto_approved(t, auto) for t in targets),
    }


def mark_waiting_changed(uid: str) -> None:
    """Recount this user's "waiting for your OK" within seconds (utils/home_assistant/waiting.py
    ``dirty_tick``), so omi_waiting_for_approval follows a new action at once. Never raises."""
    try:
        redis_db.r.sadd(WAITING_DIRTY_KEY, uid)
    except Exception:  # noqa: BLE001 — the minute tick still recounts
        record_fallback(
            component='webhook', from_mode='home_assistant', to_mode='none', reason='unavailable', outcome='degraded'
        )


def describe(row: Dict[str, Any]) -> str:
    return f"{row.get('domain')}.{row.get('service')} {', '.join(row.get('entity_ids') or [])}".strip()


def _log(uid: str, action_id: str, row: Dict[str, Any], status: str, by: str, detail: str = '') -> None:
    logger.info(
        'home_assistant: action uid=%s id=%s call=%s status=%s by=%s%s',
        uid,
        action_id,
        describe(row),
        status,
        by,
        f' detail={detail[:120]}' if detail else '',
    )


async def _execute(uid: str, action_id: str, row: Dict[str, Any], settings: Dict[str, Any], by: str) -> Dict[str, Any]:
    body = {**row['data'], 'entity_id': row['entity_ids'] if len(row['entity_ids']) > 1 else row['entity_ids'][0]}
    now = datetime.now(timezone.utc)
    try:
        changed = await client.call_service(settings, row['domain'], row['service'], body)
    except client.HomeAssistantError as error:
        fields = {'status': 'failed', 'error': error.message, 'decided_by': by, 'finished_at': now}
        await db(store.update_action, uid, action_id, fields)
        _log(uid, action_id, row, 'failed', by, error.kind)
        return {**row, **fields, 'id': action_id}
    states = [
        {'entity_id': s.get('entity_id'), 'state': s.get('state')}
        for s in changed
        if isinstance(s, dict) and s.get('entity_id') in row['entity_ids']
    ]
    fields = {'status': 'executed', 'error': '', 'decided_by': by, 'finished_at': now, 'result_states': states}
    await db(store.update_action, uid, action_id, fields)
    _log(uid, action_id, row, 'executed', by)
    return {**row, **fields, 'id': action_id}


async def request(
    uid: str,
    *,
    domain: str,
    service: str,
    entity_id: Any,
    data: Optional[Dict[str, Any]] = None,
    requested_by: str = 'chat',
) -> Dict[str, Any]:
    """An agent's call: refused, waiting for the user's OK, or run (auto-approved). Raises
    HomeAssistantError('unconfigured') when there is no Home Assistant for this user."""
    settings = await connection(uid)
    action_id = str(uuid.uuid4())
    who = str(requested_by or 'agent').strip()[:60] or 'agent'
    try:
        call = check(domain, service, entity_id, data)
    except client.HomeAssistantError as error:
        row = {
            'domain': str(domain or '')[:60],
            'service': str(service or '')[:60],
            'entity_ids': [t[:120] for t in _targets(entity_id)][:MAX_TARGETS],
            'data': {},
            'status': 'refused',
            'error': error.message,
            'requested_by': who,
        }
        await db(store.create_action, uid, action_id, row)
        _log(uid, action_id, row, 'refused', 'omi', error.message)
        return {**row, 'id': action_id}
    row = {
        **{k: call[k] for k in ('domain', 'service', 'entity_ids', 'data', 'sensitive')},
        'status': WAITING if call['needs_approval'] else 'running',
        'requested_by': who,
        'error': '',
    }
    await db(store.create_action, uid, action_id, row)
    if call['needs_approval']:
        _log(uid, action_id, row, WAITING, who)
        mark_waiting_changed(uid)
        return {**row, 'id': action_id}
    return await _execute(uid, action_id, row, settings, 'auto')


async def _waiting_row(uid: str, action_id: str) -> Dict[str, Any]:
    row = await db(store.get_action, uid, action_id)
    if not row:
        raise ActionError(404, 'Action not found')
    if row.get('status') != WAITING:
        raise ActionError(409, f"This action is {row.get('status')}, not waiting for your OK")
    if ha_settings.expired(row, datetime.now(timezone.utc)):
        await db(store.update_action, uid, action_id, {'status': 'expired'})
        _log(uid, action_id, row, 'expired', 'omi')
        raise ActionError(409, 'This action waited too long; ask again')
    return row


async def approve(uid: str, action_id: str) -> Dict[str, Any]:
    """The user's OK: the rules are checked again as they are now, then the call runs."""
    row = await _waiting_row(uid, action_id)
    try:
        check(row['domain'], row['service'], row['entity_ids'], row.get('data'))
    except client.HomeAssistantError as error:
        await db(store.update_action, uid, action_id, {'status': 'refused', 'error': error.message})
        _log(uid, action_id, row, 'refused', 'omi', error.message)
        raise ActionError(409, error.message)
    try:
        settings = await connection(uid)
    except client.HomeAssistantError as error:
        raise ActionError(409, error.message)
    started = {'status': 'running', 'approved_at': datetime.now(timezone.utc)}
    await db(store.update_action, uid, action_id, started)
    done = await _execute(uid, action_id, row, settings, 'user')
    mark_waiting_changed(uid)
    return done


async def decline(uid: str, action_id: str) -> Dict[str, Any]:
    row = await _waiting_row(uid, action_id)
    fields = {'status': 'declined', 'decided_by': 'user', 'finished_at': datetime.now(timezone.utc)}
    await db(store.update_action, uid, action_id, fields)
    _log(uid, action_id, row, 'declined', 'user')
    mark_waiting_changed(uid)
    return {**row, **fields}


def recent(uid: str, limit: int = 50) -> List[Dict[str, Any]]:
    """The action log, newest first; a waiting action past APPROVAL_TTL reads as expired."""
    now = datetime.now(timezone.utc)
    rows = store.list_actions(uid, limit=limit)
    for row in rows:
        if row.get('status') == WAITING and ha_settings.expired(row, now):
            row['status'] = 'expired'
    return rows
