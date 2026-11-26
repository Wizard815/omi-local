"""Home Assistant tools for Omi's chat agent (omi-local): the same four as the MCP tools
(utils/mcp_server/handlers/home_assistant.py), offered only to a user with a Home Assistant
connection (``home_assistant_tools_for``), after the fixed core tools so the prompt-cache
prefix stays stable. Reads are limited to what the operator allowed; a service call waits
for the user's OK unless its entities are auto-approved (utils/home_assistant/actions.py).
"""

import json
from typing import Any, Dict, List, Optional, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from utils.home_assistant import actions, client, context
from utils.home_assistant import settings as ha_settings
from utils.home_assistant.runner import db
from utils.observability.fallback import record_fallback
from utils.retrieval.tools.integration_base import resolve_config_uid

CALL_MESSAGES = {
    'waiting_for_approval': "Waiting for the user's OK in Omi (Home Assistant actions). Tell the user it waits "
    'for their OK; it has NOT happened yet.',
    'executed': 'Done: Home Assistant ran it.',
    'refused': 'Refused by Omi; nothing was sent to Home Assistant. Tell the user why.',
    'failed': 'Home Assistant did not do it.',
}


def _uid(config: Optional[RunnableConfig]) -> tuple:
    return resolve_config_uid(cast(Optional[Dict[str, Any]], config))


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


@tool
async def ha_get_states(
    domain: str = '',
    area: str = '',
    config: RunnableConfig = None,  # type: ignore[reportAssignmentType]  # langchain injects at runtime
) -> str:
    """Current states of the user's Home Assistant devices (lights, switches, climate, media players...).

    Only entities the operator allowed are visible. Use for "are the lights on?", "how warm is the
    living room?".

    Args:
        domain: Only this domain, e.g. "light" or "climate" (optional).
        area: Only this Home Assistant area, e.g. "Living Room" (optional).
    """
    uid, error = _uid(config)
    if error:
        return error
    try:
        return _json(await context.get_states(uid, domain=domain, area=area))
    except client.HomeAssistantError as failure:
        return f'Home Assistant: {failure.message}'


@tool
async def ha_whos_home(config: RunnableConfig = None) -> str:  # type: ignore[reportAssignmentType]
    """Who is home and who is away, from Home Assistant's people (when the operator shares presence)."""
    uid, error = _uid(config)
    if error:
        return error
    try:
        return _json(await context.whos_home(uid))
    except client.HomeAssistantError as failure:
        return f'Home Assistant: {failure.message}'


@tool
async def ha_area_summary(area: str = '', config: RunnableConfig = None) -> str:  # type: ignore[reportAssignmentType]
    """A short summary per Home Assistant area (lights on, temperature), or one area's devices.

    Args:
        area: One area, e.g. "Kitchen" (optional; all areas when empty).
    """
    uid, error = _uid(config)
    if error:
        return error
    try:
        return _json(await context.area_summary(uid, area=area))
    except client.HomeAssistantError as failure:
        return f'Home Assistant: {failure.message}'


@tool
async def ha_call_service(
    domain: str,
    service: str,
    entity_id: str,
    data_json: str = '',
    config: RunnableConfig = None,  # type: ignore[reportAssignmentType]
) -> str:
    """Ask Home Assistant to do something: turn lights or switches on/off, start a scene or script,
    control a media player, set the thermostat.

    Find the entity first with ha_get_states. Unless the operator auto-approved it, the call WAITS FOR
    THE USER'S OK in Omi: say it is waiting for their OK, never that it is done. Locks, alarms, covers
    and garage doors are refused unless the operator enabled them (and then always wait).

    Args:
        domain: The service's domain, e.g. "light".
        service: e.g. "turn_off", "turn_on", "toggle", "set_temperature".
        entity_id: One entity id or several, comma-separated, e.g. "light.living_room".
        data_json: Optional JSON object of simple fields, e.g. {"brightness_pct": 40} or {"temperature": 21}.
    """
    uid, error = _uid(config)
    if error:
        return error
    data: Optional[Dict[str, Any]] = None
    if data_json and data_json.strip():
        try:
            parsed = json.loads(data_json)
        except ValueError:
            return 'data_json must be a JSON object, e.g. {"brightness_pct": 40}'
        if not isinstance(parsed, dict):
            return 'data_json must be a JSON object'
        data = parsed
    try:
        row = await actions.request(
            uid, domain=domain, service=service, entity_id=entity_id, data=data, requested_by='Omi chat'
        )
    except client.HomeAssistantError as failure:
        return f'Home Assistant: {failure.message}'
    status = str(row.get('status') or '')
    detail = f" ({row['error']})" if row.get('error') else ''
    return f"{actions.describe(row)}: {status}{detail}. {CALL_MESSAGES.get(status, '')}".strip()


HOME_ASSISTANT_TOOLS = [ha_get_states, ha_whos_home, ha_area_summary, ha_call_service]
HOME_ASSISTANT_TOOL_NAMES = frozenset(t.name for t in HOME_ASSISTANT_TOOLS)


async def home_assistant_tools_for(uid: str) -> List[Any]:
    """The Home Assistant tools for a user with a connection (theirs or the stack's), else []."""
    try:
        settings = await db(ha_settings.effective, uid)
    except Exception:  # noqa: BLE001 — no settings read: chat goes on without the tools
        record_fallback(
            component='agent_tools',
            from_mode='home_assistant',
            to_mode='none',
            reason='unavailable',
            outcome='degraded',
        )
        return []
    return list(HOME_ASSISTANT_TOOLS) if ha_settings.ready(settings) else []
