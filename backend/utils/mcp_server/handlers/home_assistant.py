"""Home Assistant tools (omi-local) for MCP agents: read the allowed states, who is home, a
summary per area, and ask for a service call that waits for the user's OK unless the
operator auto-approved its entities (utils/home_assistant/).

Their ToolSpecs are in utils/mcp_server/registry.py, after the calendar tools.
Handlers run on the MCP worker thread, so each Home Assistant call runs to completion here.
"""

from typing import Any, Dict, Optional

from utils.home_assistant import actions, client, context
from utils.mcp_server.errors import ToolExecutionError
from utils.memory.product_authorization import ProductAuthorizationContext
from utils.home_assistant.runner import run_inline

ACTION_MESSAGES = {
    'waiting_for_approval': "Waiting for the user's OK in Omi; it runs once they approve it.",
    'executed': 'Done.',
    'refused': 'Refused by Omi; nothing was sent to Home Assistant.',
    'failed': 'Home Assistant did not do it.',
}


def _run(coro: Any) -> Dict[str, Any]:
    try:
        return run_inline(coro)
    except client.HomeAssistantError as error:
        raise ToolExecutionError(error.message, code=-32602 if error.kind == 'refused' else -32000)


def ha_get_states(
    uid: str, arguments: Dict[str, Any], auth_context: Optional[ProductAuthorizationContext] = None
) -> Dict[str, Any]:
    return _run(
        context.get_states(
            uid,
            domain=str(arguments.get('domain') or ''),
            area=str(arguments.get('area') or ''),
            limit=int(arguments.get('limit') or context.MAX_ENTITIES),
        )
    )


def ha_whos_home(
    uid: str, arguments: Dict[str, Any], auth_context: Optional[ProductAuthorizationContext] = None
) -> Dict[str, Any]:
    return _run(context.whos_home(uid))


def ha_area_summary(
    uid: str, arguments: Dict[str, Any], auth_context: Optional[ProductAuthorizationContext] = None
) -> Dict[str, Any]:
    return _run(context.area_summary(uid, area=str(arguments.get('area') or '')))


def ha_call_service(
    uid: str, arguments: Dict[str, Any], auth_context: Optional[ProductAuthorizationContext] = None
) -> Dict[str, Any]:
    row = _run(
        actions.request(
            uid,
            domain=arguments.get('domain') or '',
            service=arguments.get('service') or '',
            entity_id=arguments.get('entity_id') or '',
            data=arguments.get('data') if isinstance(arguments.get('data'), dict) else None,
            requested_by=arguments.get('requested_by') or 'mcp',
        )
    )
    status = str(row.get('status') or '')
    return {
        'status': status,
        'action_id': row.get('id') or '',
        'call': actions.describe(row),
        'error': row.get('error') or '',
        'message': ACTION_MESSAGES.get(status, status),
    }


_ENTITY_IDS = {
    'oneOf': [{'type': 'string'}, {'type': 'array', 'items': {'type': 'string'}, 'maxItems': actions.MAX_TARGETS}],
    'description': 'One entity id (light.living_room) or a list, all of the service\'s domain',
}

# Schemas for the ToolSpecs in utils/mcp_server/registry.py (names, titles and descriptions live there,
# where scripts/generate_mcp_tools_doc.py reads them).
GET_STATES_INPUT = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'domain': {'type': 'string', 'description': 'Only this domain, e.g. light'},
        'area': {'type': 'string', 'description': 'Only this Home Assistant area, e.g. Living Room'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': context.MAX_LIMIT, 'default': 60},
    },
}
GET_STATES_OUTPUT = {'type': 'object', 'required': ['entities'], 'properties': {'entities': {'type': 'array'}}}
WHOS_HOME_INPUT = {'type': 'object', 'additionalProperties': False, 'properties': {}}
WHOS_HOME_OUTPUT = {'type': 'object', 'required': ['people'], 'properties': {'people': {'type': 'array'}}}
AREA_SUMMARY_INPUT = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {'area': {'type': 'string', 'description': 'One area (optional)'}},
}
AREA_SUMMARY_OUTPUT = {'type': 'object'}
CALL_SERVICE_INPUT = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'domain': {'type': 'string', 'description': 'e.g. light'},
        'service': {'type': 'string', 'description': 'e.g. turn_off'},
        'entity_id': _ENTITY_IDS,
        'data': {'type': 'object', 'description': 'Simple service fields, e.g. {"brightness_pct": 40}'},
        'requested_by': {'type': 'string', 'description': 'Your name as the user knows you'},
    },
    'required': ['domain', 'service', 'entity_id'],
}
CALL_SERVICE_OUTPUT = {
    'type': 'object',
    'required': ['status'],
    'properties': {'status': {'type': 'string'}, 'action_id': {'type': 'string'}, 'message': {'type': 'string'}},
}
