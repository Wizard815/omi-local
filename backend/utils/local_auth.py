"""Self-hosted username/password login for internet-exposed deployments.

Why this exists: the app's LAN-only login (AuthService.signInWithLocalUsername
in the Dart client, backend/scripts/seed_local_account.py on this side) talks
directly to the local Firebase Auth *emulator* over a plain, non-standard port
(9099). That works fine on a LAN but cannot be reached through a normal
Cloudflare Tunnel (or any HTTPS-only reverse proxy) — the emulator's SDK path
always builds a bare http://host:port URL, and edge proxies like Cloudflare
only forward standard HTTPS (443) by hostname.

This module is the fully self-hosted fix: credentials are checked here
(argon2 hash, rate-limited, generic errors — this endpoint is the one thing
directly reachable from the open internet, so it gets the scrutiny that
implies), and on success this backend mints its own signed session token —
no Firebase project, no Google infrastructure, nothing leaves this box at
any point. utils/other/endpoints.py's verify_token() recognizes this token
type alongside Firebase ID tokens, so the rest of the backend (all ~69 files
that depend on that one function) needs no changes to accept it. The token
carries the same `uid` the Firebase-emulator LAN account already uses (see
seed_local_account.py), so both login paths reach the same account and data.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from fastapi import HTTPException

from database.redis_db import r as redis_client
from database._client import get_firestore_client

_LOCAL_ACCOUNTS_COLLECTION = 'local_login_accounts'

# Deliberately expensive (argon2id defaults: 64 MiB memory, 3 iterations) —
# this endpoint is only ever called a handful of times a day by the real
# owner, so hashing cost is not a UX concern, only an attacker's.
_hasher = PasswordHasher()

# Rate limiting: keyed on the request IP so a distributed attempt still costs
# an attacker one bucket per source, and separately on the attempted username
# so a single compromised/leaked IP (e.g. shared NAT, VPN egress) can't be
# used to hammer a specific account from many source IPs either.
_MAX_ATTEMPTS_PER_WINDOW = 5
_WINDOW_SECONDS = 300  # 5 minutes
_LOCKOUT_SECONDS = 900  # 15 minutes once the window is exceeded


@dataclass
class LocalAccount:
    username: str
    uid: str
    password_hash: str


def _account_doc(username: str):
    return get_firestore_client().collection(_LOCAL_ACCOUNTS_COLLECTION).document(username.strip().lower())


def get_local_account(username: str) -> Optional[LocalAccount]:
    snap = _account_doc(username).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    uid = data.get('uid')
    password_hash = data.get('password_hash')
    if not uid or not password_hash:
        return None
    return LocalAccount(username=username.strip().lower(), uid=uid, password_hash=password_hash)


def set_local_account(username: str, uid: str, password: str) -> None:
    """Create or update a local login account. Called only from the seed
    script (backend/scripts/seed_local_account.py), never from a request."""
    _account_doc(username).set(
        {
            'username': username.strip().lower(),
            'uid': uid,
            'password_hash': hash_password(password),
        }
    )


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHash):
        return False


def _rate_limit_key(kind: str, identifier: str) -> str:
    return f'local_auth:attempts:{kind}:{identifier}'


def _check_and_record_attempt(identifier: str, *, kind: str) -> None:
    """Raise 429 if this identifier (IP or username) is over the attempt
    budget; otherwise record this attempt. Called once per axis (IP and
    username) so both a single attacker IP and a single targeted account are
    independently throttled."""
    key = _rate_limit_key(kind, identifier)
    lock_key = f'{key}:locked'
    if redis_client.get(lock_key):
        raise HTTPException(status_code=429, detail='Too many attempts. Try again later.')

    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, _WINDOW_SECONDS)
    if count > _MAX_ATTEMPTS_PER_WINDOW:
        redis_client.set(lock_key, '1', ex=_LOCKOUT_SECONDS)
        raise HTTPException(status_code=429, detail='Too many attempts. Try again later.')


def enforce_login_rate_limit(*, client_ip: str, username: str) -> None:
    # client_ip must be request.client.host (the real TCP peer), never a
    # forwarded-for/CF-Connecting-IP header — this codebase deliberately
    # never trusts those for rate-limit subjects (see
    # routers/public_shared_conversation_chat.py) since any direct caller can
    # set them to whatever they like. Behind a reverse proxy this collapses
    # every remote caller to one IP bucket, which is exactly why the
    # username axis below — the actually load-bearing one here, since there
    # are only ever a handful of valid usernames — doesn't depend on it.
    _check_and_record_attempt(client_ip, kind='ip')
    _check_and_record_attempt(username.strip().lower(), kind='username')


def _clear_rate_limit(*, client_ip: str, username: str) -> None:
    for kind, identifier in (('ip', client_ip), ('username', username.strip().lower())):
        key = _rate_limit_key(kind, identifier)
        redis_client.delete(key)
        redis_client.delete(f'{key}:locked')


_JWT_ALGORITHM = 'HS256'
_JWT_ISSUER = 'omi-local-auth'
_SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days — no refresh endpoint yet, re-login after this


def _jwt_secret(*, required: bool) -> Optional[str]:
    secret = os.environ.get('LOCAL_AUTH_JWT_SECRET', '').strip()
    if not secret and required:
        raise HTTPException(
            status_code=503,
            detail='Remote login is not configured on this server: LOCAL_AUTH_JWT_SECRET is not set.',
        )
    return secret or None


def create_remote_login_token(uid: str) -> str:
    secret = _jwt_secret(required=True)
    now = int(time.time())
    payload = {'uid': uid, 'iat': now, 'exp': now + _SESSION_TTL_SECONDS, 'iss': _JWT_ISSUER}
    return jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


def decode_local_session_token(token: str) -> Optional[str]:
    """Return the uid encoded in a locally-issued session token, or None if
    this isn't one of ours (a real Firebase ID token, for instance — those
    are RS256-signed and simply fail this HS256 decode) or the secret isn't
    configured (LAN-only deployments) or the token is invalid/expired. Called
    unconditionally by verify_token() for every request, so it must never
    raise — a missing/wrong token here just means "not a local session,
    check something else," not an error."""
    secret = _jwt_secret(required=False)
    if secret is None:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=[_JWT_ALGORITHM], issuer=_JWT_ISSUER)
    except jwt.PyJWTError:
        return None
    uid = payload.get('uid')
    return uid if isinstance(uid, str) and uid else None


def authenticate_local_account(username: str, password: str, *, client_ip: str) -> str:
    """Full login flow: rate-limit, verify credentials, mint a session token.

    Raises HTTPException(429) if rate-limited, HTTPException(401) with a
    generic message on any credential failure (never reveals whether the
    username exists), or returns a signed session token on success.
    """
    enforce_login_rate_limit(client_ip=client_ip, username=username)

    account = get_local_account(username)
    # Always run a hash verification, even for an unknown username, against a
    # fixed dummy hash — otherwise a missing account returns faster than a
    # wrong password and the response-time difference leaks which usernames
    # exist.
    dummy_hash = (
        '$argon2id$v=19$m=65536,t=3,p=4$'
        'c29tZXNhbHR2YWx1ZTEyMzQ1Njc4$c29tZWhhc2h2YWx1ZTEyMzQ1Njc4OTAxMjM0NTY3ODkw'
    )
    password_ok = verify_password(password, account.password_hash if account else dummy_hash)

    if not account or not password_ok:
        raise HTTPException(status_code=401, detail='Invalid username or password')

    _clear_rate_limit(client_ip=client_ip, username=username)
    return create_remote_login_token(account.uid)
