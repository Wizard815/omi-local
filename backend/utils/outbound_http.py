"""HTTP for Omi's outbound integrations (omi-local): the wiki, Hermes, Odysseus, Studio,
Coinpile and Pondering Orb.

Every target URL goes through the same SSRF rule as the user's webhooks
(utils/http_client.safe_request_target): a public host, or a private LAN address the
operator listed in OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS; loopback, link-local (cloud
metadata) and unlisted private addresses are refused. The request connects to the address
that was checked (no second DNS lookup). omi-host's egress guard already lets LAN addresses
through, so nothing needs to go into OMI_EGRESS_ALLOW for these.

``mcp_call`` is one stateless MCP JSON-RPC ``tools/call`` (the wiki's /family-mcp,
Coinpile's /api/v1/mcp and Orb's /mcp all accept that without a session).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Tuple

from utils.executors import db_executor, run_blocking
from utils.http_client import (
    UnsafeWebhookURLError,
    get_pinned_delivery_client,
    get_webhook_semaphore,
    safe_request_target,
)
from utils.log_sanitizer import sanitize

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_ERROR_TEXT = 300


class OutboundError(Exception):
    """A send that did not happen. ``kind``: blocked (address not allowed), unreachable,
    http (the target answered an error), refused (the target said no, e.g. an MCP tool error)."""

    def __init__(self, kind: str, message: str, status_code: int = 0):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code


def _target(url: str) -> Tuple[str, Dict[str, Any]]:
    """(pinned_url, extra) for ``url``; a seam tests replace to reach their loopback fakes."""
    return safe_request_target(url)


async def request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    form: Optional[Dict[str, str]] = None,
    content: Optional[bytes] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[int, Any]:
    """One request to an allowed target. Returns (status, parsed JSON or text)."""
    try:
        pinned_url, extra = await run_blocking(db_executor, _target, url)
    except UnsafeWebhookURLError as error:
        raise OutboundError(
            'blocked',
            f'{error}. A server on your network must be listed in OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS '
            '(see omi-host/examples/integrations/README.md).',
        ) from error
    merged = {**(extra.get('headers') or {}), **(headers or {})}
    kwargs: Dict[str, Any] = {'headers': merged, 'extensions': extra.get('extensions') or {}, 'timeout': timeout}
    if json_body is not None:
        kwargs['json'] = json_body
    elif form is not None:
        kwargs['data'] = form
    elif content is not None:
        kwargs['content'] = content
    try:
        async with get_webhook_semaphore():
            response = await get_pinned_delivery_client().request(method, pinned_url, **kwargs)
    except Exception as error:  # noqa: BLE001 — any transport failure is "unreachable" to the user
        raise OutboundError('unreachable', f'Could not reach {_host(url)}: {type(error).__name__}') from error
    text = response.text
    try:
        body: Any = response.json()
    except (ValueError, json.JSONDecodeError):
        body = text
    return response.status_code, body


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc or url


def error_text(body: Any) -> str:
    """A short, sanitized reason from an error body (never the whole body)."""
    if isinstance(body, dict):
        for key in ('detail', 'error', 'message'):
            value = body.get(key)
            if isinstance(value, dict):
                value = value.get('message') or value.get('code')
            if value:
                return sanitize(str(value))[:MAX_ERROR_TEXT]
    return sanitize(str(body or ''))[:MAX_ERROR_TEXT]


async def request_ok(method: str, url: str, **kwargs: Any) -> Any:
    """``request``, raising OutboundError('http') on a non-2xx answer."""
    status, body = await request(method, url, **kwargs)
    if status >= 400:
        raise OutboundError('http', f'{_host(url)} answered {status}: {error_text(body)}', status)
    return body


async def mcp_call(
    url: str, token: str, tool: str, arguments: Dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> str:
    """Call one MCP tool (stateless JSON-RPC over POST, Bearer token). Returns the tool's text;
    raises OutboundError('refused') when the tool answered isError."""
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': arguments}}
    headers = {'Accept': 'application/json, text/event-stream'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    answer = await request_ok('POST', url, headers=headers, json_body=body, timeout=timeout)
    if not isinstance(answer, dict):
        raise OutboundError('http', f'{_host(url)} did not answer JSON-RPC')
    if answer.get('error'):
        raise OutboundError('refused', error_text(answer['error']))
    result = answer.get('result') or {}
    text = '\n'.join(
        str(part.get('text') or '') for part in (result.get('content') or []) if isinstance(part, dict)
    ).strip()
    if result.get('isError'):
        raise OutboundError('refused', sanitize(text)[:MAX_ERROR_TEXT] or f'{tool} failed')
    return text


async def mcp_ping(url: str, token: str) -> None:
    """``initialize`` (every MCP server answers it): proves the address and the token."""
    headers = {'Accept': 'application/json, text/event-stream'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'omi', 'version': '1'}},
    }
    answer = await request_ok('POST', url, headers=headers, json_body=body)
    if not isinstance(answer, dict) or answer.get('error'):
        raise OutboundError('refused', error_text((answer or {}).get('error') if isinstance(answer, dict) else answer))


def run_sync(coro: Any) -> Any:
    """Run an outbound coroutine from a worker thread (approval side effects run on the
    post-processing pool, never on the event loop)."""
    return asyncio.run(coro)
