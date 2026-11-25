"""Serve /_local/storage only to someone allowed to read the file.

The local blob store holds recordings, speech profiles and chat files. It is
mounted on the same port a tunnel may expose, so every request needs one of:

- a signed URL (``?exp=&sig=``) minted by ``LocalBlob.generate_signed_url``.
  The API only mints these inside routes that already checked the caller owns
  the data, and the signature covers this one blob and an expiry. Audio players
  and image widgets use these, since they can't send headers.
- ``Authorization: Bearer <token>`` (the same login the API takes) for a blob
  whose path starts with the caller's uid. Anyone else's blob answers 404.

Everything else is refused: 401 with no credentials or a bad login, 403 for a
bad or expired signature, 404 for traversal, hidden paths and other users'
files. Responses are marked ``private, no-store`` so a tunnel or CDN never
caches a recording.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from utils.other.local_storage import verify_local_storage_signature

logger = logging.getLogger(__name__)

TokenVerifier = Callable[[str], str]


def _default_verify_token(token: str) -> str:
    from utils.other.endpoints import verify_token

    return verify_token(token)


def _route_path(scope: dict[str, Any]) -> str:
    path = scope.get('path', '')
    root_path = scope.get('root_path', '')
    # Starlette >= 0.33 keeps the full path and puts the mount prefix in root_path.
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :]
    return path


def _split_blob_path(route_path: str) -> tuple[str, str] | None:
    """Return (bucket, blob name), or None for anything that isn't a plain file path."""

    if '\\' in route_path or '\x00' in route_path:
        return None
    parts = route_path.lstrip('/').split('/')
    if len(parts) < 2:
        return None
    if any(part in {'', '.', '..'} or part.startswith('.') for part in parts):
        return None
    return parts[0], '/'.join(parts[1:])


class LocalStorageGate:
    def __init__(self, directory: Path, *, verify_token: TokenVerifier | None = None):
        self._files = StaticFiles(directory=directory)
        self._verify_token = verify_token or _default_verify_token

    async def __call__(
        self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]
    ) -> None:
        if scope['type'] != 'http':
            return
        if scope.get('method') not in {'GET', 'HEAD'}:
            await _reply(send, 405, 'Only GET and HEAD work here.', extra=[(b'allow', b'GET, HEAD')])
            return

        blob = _split_blob_path(_route_path(scope))
        if blob is None:
            await _reply(send, 404, 'File not found.')
            return
        bucket, name = blob

        allowed, status, detail = await self._authorize(scope, bucket, name)
        if not allowed:
            await _reply(send, status, detail)
            return

        async def send_private(message: dict[str, Any]) -> None:
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'cache-control']
                headers.append((b'cache-control', b'private, no-store'))
                message = {**message, 'headers': headers}
            await send(message)

        await self._files(scope, receive, send_private)

    async def _authorize(self, scope: dict[str, Any], bucket: str, name: str) -> tuple[bool, int, str]:
        query = parse_qs(scope.get('query_string', b'').decode('latin-1'))
        signature = (query.get('sig') or [''])[0]
        expires = (query.get('exp') or [''])[0]
        authorization = ''
        for key, value in scope.get('headers', []):
            if key.lower() == b'authorization':
                authorization = value.decode('latin-1')
                break

        if signature and verify_local_storage_signature(bucket, name, expires, signature):
            return True, 200, ''

        if authorization:
            scheme, _, token = authorization.partition(' ')
            if scheme.lower() != 'bearer' or not token.strip():
                return False, 401, 'Sign in again: the login token is missing or malformed.'
            try:
                uid = await run_in_threadpool(self._verify_token, token.strip())
            except Exception:  # noqa: BLE001 — any verification failure is a refused login
                return False, 401, 'Sign in again: the login token was not accepted.'
            if uid and name.split('/', 1)[0] == uid:
                return True, 200, ''
            # Not yours (or not a per-user path): don't confirm it exists.
            return False, 404, 'File not found.'

        if signature or expires:
            return False, 403, 'This link has expired or is not valid. Open the item in the app again to get a new one.'
        return False, 401, 'Sign in to open this file.'


async def _reply(
    send: Callable[..., Awaitable[Any]], status: int, detail: str, extra: list[tuple[bytes, bytes]] | None = None
) -> None:
    body = json.dumps({'detail': detail}).encode()
    headers = [
        (b'content-type', b'application/json'),
        (b'content-length', str(len(body)).encode()),
        (b'cache-control', b'private, no-store'),
    ]
    if status == 401:
        headers.append((b'www-authenticate', b'Bearer'))
    headers.extend(extra or [])
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': body})
