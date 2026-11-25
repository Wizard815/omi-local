"""Signed links for the self-hosted blob store mounted at ``/_local/storage``.

The store holds recordings, speech profiles and chat files, and the port it is served on is
often reachable from the LAN or through a tunnel. A link is ``<base>/<bucket>/<name>?sig=...``
(plus ``exp=`` when the caller asked for an expiry), where ``sig`` is an HMAC of the path and
expiry under a key only this server holds. ``SignedStaticFiles`` refuses any other request.

Buckets that hold public app art (``BUCKET_PLUGINS_LOGOS``, ``BUCKET_APP_THUMBNAILS``) stay
unsigned. ``OMI_LOCAL_STORAGE_ALLOW_UNSIGNED=1`` lets old unsigned links (already saved in
Firestore before this change) keep working, at the cost of the protection.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import posixpath
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

URL_KEY_ENV = 'OMI_LOCAL_STORAGE_URL_KEY'
ALLOW_UNSIGNED_ENV = 'OMI_LOCAL_STORAGE_ALLOW_UNSIGNED'
PUBLIC_BUCKET_ENVS = ('BUCKET_PLUGINS_LOGOS', 'BUCKET_APP_THUMBNAILS')
_KEY_FILE = ('secrets', 'local_storage_url_key')

_key_lock = threading.Lock()
_cached_key: bytes | None = None


def _key_path() -> Path | None:
    state_root = os.environ.get('OMI_HARNESS_STATE_ROOT', '').strip()
    return Path(state_root).expanduser().joinpath(*_KEY_FILE) if state_root else None


def _load_or_create_key() -> bytes:
    from_env = os.environ.get(URL_KEY_ENV, '').strip()
    if from_env:
        if len(from_env) < 32:
            raise RuntimeError(f'{URL_KEY_ENV} must be at least 32 characters')
        return from_env.encode('utf-8')
    path = _key_path()
    if path is None:
        raise RuntimeError(f'{URL_KEY_ENV} or OMI_HARNESS_STATE_ROOT is required to sign local storage links')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_hex(32))
    key = path.read_text().strip()
    if len(key) < 32:
        raise RuntimeError(f'{path} holds a key shorter than 32 characters; delete it to make a new one')
    return key.encode('utf-8')


def url_key() -> bytes:
    global _cached_key
    with _key_lock:
        if _cached_key is None:
            _cached_key = _load_or_create_key()
        return _cached_key


def reset_url_key_cache() -> None:
    """Forget the cached key (tests, or after the key file is replaced)."""
    global _cached_key
    with _key_lock:
        _cached_key = None


def is_public_bucket(bucket: str) -> bool:
    return any(bucket and os.environ.get(name, '').strip() == bucket for name in PUBLIC_BUCKET_ENVS)


def _expiry_epoch(expiration) -> int | None:
    """Accept what ``google.cloud.storage.Blob.generate_signed_url`` accepts."""
    if expiration is None:
        return None
    if isinstance(expiration, datetime.timedelta):
        return int(time.time() + expiration.total_seconds())
    if isinstance(expiration, datetime.datetime):
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=datetime.timezone.utc)
        return int(expiration.timestamp())
    seconds = int(expiration)
    # GCS treats a bare int as an absolute epoch in v2 and as a lifetime in v4; a value this
    # small is a lifetime either way.
    return seconds if seconds > 10**9 else int(time.time()) + seconds


def _signature(path: str, exp: int | None) -> str:
    message = f'{path}\n{exp if exp is not None else ""}'.encode('utf-8')
    return hmac.new(url_key(), message, hashlib.sha256).hexdigest()


def signed_query(bucket: str, name: str, expiration=None) -> str:
    """The query string (without ``?``) for a link to ``bucket/name``, or '' for a public bucket."""
    if is_public_bucket(bucket):
        return ''
    exp = _expiry_epoch(expiration)
    sig = _signature(posixpath.normpath(f'{bucket}/{name}'), exp)
    return f'exp={exp}&sig={sig}' if exp is not None else f'sig={sig}'


def verify(path: str, query_string: bytes) -> bool:
    """True when ``path`` (bucket/name, as the static mount resolved it) may be served."""
    path = posixpath.normpath(path.lstrip('/'))
    bucket = path.split('/', 1)[0]
    if is_public_bucket(bucket):
        return True
    params = parse_qs(query_string.decode('latin-1'))
    sig = (params.get('sig') or [''])[0]
    raw_exp = (params.get('exp') or [''])[0]
    if sig:
        try:
            exp = int(raw_exp) if raw_exp else None
        except ValueError:
            return False
        if exp is not None and exp < time.time():
            return False
        return hmac.compare_digest(sig, _signature(path, exp))
    return os.environ.get(ALLOW_UNSIGNED_ENV, '').strip().lower() in {'1', 'true', 'yes'}


class SignedStaticFiles(StaticFiles):
    """``StaticFiles`` that serves only signed links (see module docstring)."""

    async def get_response(self, path: str, scope):
        if not verify(path, scope.get('query_string', b'')):
            return PlainTextResponse('This link is missing a valid signature or has expired.', status_code=403)
        return await super().get_response(path, scope)
