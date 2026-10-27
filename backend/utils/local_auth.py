"""Self-hosted username/password login: the only way to sign in to omi-local.

The app used to have a second, LAN-only login that signed in to the local
Firebase Auth *emulator* directly on port 9099. The emulator's tokens are
unsigned, so a backend that accepted them let anyone who could reach it write
a token for any uid (see utils/self_hosted_id_tokens.py). Both the LAN and the
internet now sign in here, and the emulator port is no longer published.

Credentials are checked here (argon2 hash, rate-limited, generic errors —
this endpoint is the one thing directly reachable from the open internet, so
it gets the scrutiny that implies), and on success this backend mints its own
signed session token —
no Firebase project, no Google infrastructure, nothing leaves this box at
any point. utils/other/endpoints.py's verify_token() recognizes this token
type, so the rest of the backend (all ~69 files that depend on that one
function) needs no changes to accept it. The token carries the uid of the
account's Firebase-emulator user (see seed_local_account.py), so data stored
under the old LAN login stays with the account.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from fastapi import HTTPException

# Module references, resolved at call time: endpoints.verify_token imports this
# module, and its isolated tests stub these database modules with only the names
# they use.
from database import _client as firestore_client_module
from database import redis_db

_LOCAL_ACCOUNTS_COLLECTION = 'local_login_accounts'
_EMULATOR_TIMEOUT_SECONDS = 5.0

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
    return (
        firestore_client_module.get_firestore_client()
        .collection(_LOCAL_ACCOUNTS_COLLECTION)
        .document(username.strip().lower())
    )


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


def local_account_email(username: str) -> str:
    """The emulator user's email for a username (the app never shows it)."""
    return f'{username.strip().lower()}@local.omi'


def set_local_account(username: str, uid: str, password: str) -> None:
    """Create or update a local login account: from the seed script
    (backend/scripts/seed_local_account.py), or when an emulator-only account
    first signs in here (see _emulator_account_uid)."""
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
    if redis_db.r.get(lock_key):
        raise HTTPException(status_code=429, detail='Too many attempts. Try again later.')

    count = redis_db.r.incr(key)
    if count == 1:
        redis_db.r.expire(key, _WINDOW_SECONDS)
    if count > _MAX_ATTEMPTS_PER_WINDOW:
        redis_db.r.set(lock_key, '1', ex=_LOCKOUT_SECONDS)
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
        redis_db.r.delete(key)
        redis_db.r.delete(f'{key}:locked')


_JWT_ALGORITHM = 'HS256'
_JWT_ISSUER = 'omi-local-auth'
_SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days — no refresh endpoint yet, re-login after this


def _jwt_secret(*, required: bool) -> Optional[str]:
    secret = os.environ.get('LOCAL_AUTH_JWT_SECRET', '').strip()
    if not secret and required:
        raise HTTPException(
            status_code=503,
            detail='Login is not configured on this server: LOCAL_AUTH_JWT_SECRET is not set.',
        )
    return secret or None


def _require_jwt_secret() -> str:
    secret = _jwt_secret(required=True)
    if secret is None:  # unreachable: required=True raises instead
        raise HTTPException(status_code=503, detail='Login is not configured on this server.')
    return secret


def create_remote_login_token(uid: str) -> str:
    secret = _require_jwt_secret()
    now = int(time.time())
    payload = {'uid': uid, 'iat': now, 'exp': now + _SESSION_TTL_SECONDS, 'iss': _JWT_ISSUER}
    return jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


def decode_local_session_token(token: str) -> Optional[str]:
    """Return the uid encoded in a locally-issued session token, or None if
    this isn't one of ours (a real Firebase ID token, for instance — those
    are RS256-signed and simply fail this HS256 decode) or the secret isn't
    configured or the token is invalid/expired. Called
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


def _emulator_account_uid(username: str, password: str) -> Optional[str]:
    """uid of the Firebase Auth emulator user with this username and password.

    Accounts seeded for the old LAN login (or with the old --lan-only flag)
    exist only in the emulator. Checking the password there lets them sign in
    here once, after which they have their own entry. Only a self-hosted
    emulator stack asks, over the container's loopback: the emulator's port is
    not published.
    """
    from utils.self_hosted_id_tokens import self_hosted_id_token_guard_enabled

    if not self_hosted_id_token_guard_enabled():
        return None
    host = os.environ['FIREBASE_AUTH_EMULATOR_HOST'].strip()
    try:
        response = httpx.post(
            f'http://{host}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=omi-local',
            json={'email': local_account_email(username), 'password': password, 'returnSecureToken': True},
            timeout=_EMULATOR_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    uid = response.json().get('localId')
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
        '$argon2id$v=19$m=65536,t=3,p=4$' 'c29tZXNhbHR2YWx1ZTEyMzQ1Njc4$c29tZWhhc2h2YWx1ZTEyMzQ1Njc4OTAxMjM0NTY3ODkw'
    )
    password_ok = verify_password(password, account.password_hash if account else dummy_hash)

    if account is None:
        emulator_uid = _emulator_account_uid(username, password)
        if emulator_uid is not None:
            set_local_account(username, emulator_uid, password)
            _clear_rate_limit(client_ip=client_ip, username=username)
            return create_remote_login_token(emulator_uid)

    if not account or not password_ok:
        raise HTTPException(status_code=401, detail='Invalid username or password')

    _clear_rate_limit(client_ip=client_ip, username=username)
    return create_remote_login_token(account.uid)
