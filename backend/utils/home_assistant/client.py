"""Home Assistant's REST API (omi-local), as Omi uses it.

Every call goes through utils/outbound_http.py: the same SSRF rule as webhooks (a LAN
address must be listed in OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS; loopback and cloud metadata
are refused), the connection pinned to the checked address, and a short timeout because
Home Assistant is on the LAN (OMI_HA_TIMEOUT_SECONDS, default 5 s).

The long-lived access token goes in the Authorization header only. Errors never carry it:
they say what failed (blocked, unreachable, auth, http) and a short sanitized reason.

Endpoints (https://developers.home-assistant.io/docs/api/rest/):
GET /api/ (probe), GET /api/states, POST /api/services/<domain>/<service>,
POST /api/events/<event_type>, POST /api/template.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from utils import outbound_http
from utils.home_assistant import settings as ha_settings
from utils.outbound_http import OutboundError, error_text


class HomeAssistantError(Exception):
    """A Home Assistant call that did not happen. ``kind``: unconfigured, blocked,
    unreachable, auth (the token was refused), http (an error answer), refused (Omi's own
    rules said no: domain, entity or service not allowed)."""

    def __init__(self, kind: str, message: str, status_code: int = 0):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code


async def call(
    settings: Dict[str, Any],
    method: str,
    path: str,
    *,
    json_body: Any = None,
    timeout: Optional[float] = None,
) -> Any:
    if not ha_settings.ready(settings):
        raise HomeAssistantError('unconfigured', 'Home Assistant is not set up (OMI_HA_URL and a token)')
    headers = {'Authorization': f"Bearer {settings['token']}", 'Content-Type': 'application/json'}
    try:
        status, body = await outbound_http.request(
            method,
            f"{settings['url']}{path}",
            headers=headers,
            json_body=json_body,
            timeout=timeout or ha_settings.timeout(),
        )
    except OutboundError as error:
        raise HomeAssistantError(error.kind, error.message) from error
    if status in (401, 403):
        raise HomeAssistantError('auth', 'Home Assistant refused the access token', status)
    if status >= 400:
        raise HomeAssistantError('http', f'Home Assistant answered {status}: {error_text(body)}', status)
    return body


async def ping(settings: Dict[str, Any]) -> str:
    body = await call(settings, 'GET', '/api/')
    return str((body or {}).get('message') or 'API running') if isinstance(body, dict) else 'API running'


async def states(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    body = await call(settings, 'GET', '/api/states')
    if not isinstance(body, list):
        raise HomeAssistantError('http', 'Home Assistant did not answer a list of states')
    return [row for row in body if isinstance(row, dict) and isinstance(row.get('entity_id'), str)]


async def call_service(settings: Dict[str, Any], domain: str, service: str, data: Dict[str, Any]) -> List[Any]:
    body = await call(settings, 'POST', f'/api/services/{domain}/{service}', json_body=data)
    if isinstance(body, dict):  # return_response services answer an object
        return list(body.get('changed_states') or [])
    return body if isinstance(body, list) else []


async def fire_event(
    settings: Dict[str, Any], event_type: str, data: Dict[str, Any], *, timeout: Optional[float] = None
) -> None:
    await call(settings, 'POST', f'/api/events/{event_type}', json_body=data, timeout=timeout)


async def template_json(settings: Dict[str, Any], template: str) -> Any:
    """Render a template that ends in ``| tojson`` and parse it (HA answers text/plain)."""
    body = await call(settings, 'POST', '/api/template', json_body={'template': template})
    if not isinstance(body, str):
        return body  # the text was JSON and came back parsed
    try:
        return json.loads(body)
    except ValueError as error:
        raise HomeAssistantError('http', 'Home Assistant did not render the template as JSON') from error
