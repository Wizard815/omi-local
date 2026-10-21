"""Accounts that exist only in the Firebase Auth emulator can still sign in.

Before /v1/auth/local-login was the only sign-in, an account could be seeded
for the LAN login alone (the emulator user, no local_login_accounts entry).
Those accounts sign in here once, by password against the emulator over the
container's loopback, and keep their uid. An account that has an entry is
checked against its own hash only, so the emulator can never override it.
"""

import httpx
import jwt
import pytest
from fastapi import HTTPException

import database.redis_db as redis_db
import utils.local_auth as local_auth

SECRET = 'test-local-auth-secret-0123456789abcdef'


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def expire(self, key, seconds):
        return True

    def set(self, key, value, ex=None):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class _Emulator:
    """Answers accounts:signInWithPassword like the Auth emulator."""

    def __init__(self, users=None, down=False):
        self.users = users or {}
        self.down = down
        self.requests = []

    def post(self, url, json, timeout):
        self.requests.append((url, json))
        if self.down:
            raise httpx.ConnectError('connection refused')
        uid = self.users.get((json['email'], json['password']))
        request = httpx.Request('POST', url)
        if uid is None:
            return httpx.Response(400, json={'error': {'message': 'INVALID_LOGIN_CREDENTIALS'}}, request=request)
        return httpx.Response(200, json={'localId': uid, 'idToken': 'unsigned'}, request=request)


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', '127.0.0.1:9099')
    monkeypatch.setenv('LOCAL_AUTH_JWT_SECRET', SECRET)
    monkeypatch.setattr(redis_db, 'r', _FakeRedis())
    accounts = {}
    monkeypatch.setattr(local_auth, 'get_local_account', lambda username: accounts.get(username.strip().lower()))

    def _store(username, uid, password):
        key = username.strip().lower()
        accounts[key] = local_auth.LocalAccount(key, uid, local_auth.hash_password(password))

    monkeypatch.setattr(local_auth, 'set_local_account', _store)
    emulator = _Emulator(users={('blake@local.omi', 'pw-123456'): 'emulator-uid'})
    monkeypatch.setattr(local_auth.httpx, 'post', emulator.post)
    return accounts, emulator


def _uid(token):
    return jwt.decode(token, SECRET, algorithms=['HS256'], issuer='omi-local-auth')['uid']


def test_an_emulator_only_account_signs_in_and_keeps_its_uid(stack):
    accounts, emulator = stack

    token = local_auth.authenticate_local_account('Blake', 'pw-123456', client_ip='10.0.0.2')

    assert _uid(token) == 'emulator-uid'
    assert emulator.requests[0][0].startswith('http://127.0.0.1:9099/identitytoolkit.googleapis.com/')
    assert accounts['blake'].uid == 'emulator-uid'
    # From now on its own entry answers, without asking the emulator.
    emulator.down = True
    assert _uid(local_auth.authenticate_local_account('blake', 'pw-123456', client_ip='10.0.0.2')) == 'emulator-uid'


def test_a_wrong_password_for_an_emulator_only_account_is_rejected(stack):
    accounts, _ = stack
    with pytest.raises(HTTPException) as rejected:
        local_auth.authenticate_local_account('blake', 'wrong-password', client_ip='10.0.0.2')
    assert rejected.value.status_code == 401
    assert accounts == {}


def test_an_unreachable_emulator_is_an_ordinary_rejection(stack):
    _, emulator = stack
    emulator.down = True
    with pytest.raises(HTTPException) as rejected:
        local_auth.authenticate_local_account('blake', 'pw-123456', client_ip='10.0.0.2')
    assert rejected.value.status_code == 401


def test_an_existing_entry_is_never_overridden_by_the_emulator(stack):
    accounts, emulator = stack
    accounts['blake'] = local_auth.LocalAccount('blake', 'data-uid', local_auth.hash_password('current-password'))

    with pytest.raises(HTTPException):
        local_auth.authenticate_local_account('blake', 'pw-123456', client_ip='10.0.0.2')
    assert emulator.requests == []
    assert _uid(local_auth.authenticate_local_account('blake', 'current-password', client_ip='10.0.0.2')) == 'data-uid'


def test_only_a_self_hosted_emulator_stack_asks_the_emulator(stack, monkeypatch):
    _, emulator = stack
    monkeypatch.delenv('OMI_SELF_HOSTED')
    with pytest.raises(HTTPException):
        local_auth.authenticate_local_account('blake', 'pw-123456', client_ip='10.0.0.2')
    assert emulator.requests == []
