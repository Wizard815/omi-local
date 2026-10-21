"""utils/self_hosted_id_tokens.py: a self-hosted emulator stack refuses forged tokens.

With FIREBASE_AUTH_EMULATOR_HOST set, firebase_admin verifies no signature, so
an unsigned token naming any uid passed as that user. The first test shows
that against the real firebase_admin, then shows the installed guard refusing
the same token while still accepting the session tokens /v1/auth/local-login
signs. The rest pin when the guard installs and that the production
verify_token path answers a forged token with a 401.
"""

import base64
import json
import time
import types

import firebase_admin
import jwt
import pytest
from fastapi import HTTPException
from firebase_admin import auth as firebase_auth

from utils.self_hosted_id_tokens import install_self_hosted_id_token_guard, self_hosted_id_token_guard_enabled

PROJECT = 'demo-omi-local'
SECRET = 'test-local-auth-secret-0123456789abcdef'
SELF_HOSTED_EMULATOR = {'OMI_SELF_HOSTED': '1', 'FIREBASE_AUTH_EMULATOR_HOST': '127.0.0.1:9099'}


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()


def forged_emulator_token(uid: str) -> str:
    now = int(time.time())
    claims = {
        'iss': f'https://securetoken.google.com/{PROJECT}',
        'aud': PROJECT,
        'auth_time': now,
        'user_id': uid,
        'sub': uid,
        'iat': now,
        'exp': now + 3600,
    }
    return _b64({'alg': 'none', 'typ': 'JWT'}) + '.' + _b64(claims) + '.'


def local_session_token(uid: str, secret: str = SECRET) -> str:
    now = int(time.time())
    return jwt.encode({'uid': uid, 'iat': now, 'exp': now + 3600, 'iss': 'omi-local-auth'}, secret, algorithm='HS256')


@pytest.fixture
def self_hosted_env(monkeypatch):
    for key, value in SELF_HOSTED_EMULATOR.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('LOCAL_AUTH_JWT_SECRET', SECRET)


@pytest.fixture
def emulator_app():
    app = firebase_admin.initialize_app(options={'projectId': PROJECT}, name='self-hosted-id-token-test')
    yield app
    firebase_admin.delete_app(app)


@pytest.fixture
def default_emulator_app():
    """verify_token checks against the default app, as the backend does."""
    try:
        existing = firebase_admin.get_app()
    except ValueError:
        existing = None
    app = existing or firebase_admin.initialize_app(options={'projectId': PROJECT})
    yield app
    if existing is None:
        firebase_admin.delete_app(app)


def test_forged_emulator_token_is_refused_once_the_guard_is_installed(self_hosted_env, emulator_app, monkeypatch):
    forged = forged_emulator_token('victim-uid')
    # Restored after the test: the guard replaces this module attribute.
    monkeypatch.setattr(firebase_auth, 'verify_id_token', firebase_auth.verify_id_token)

    # The hole: firebase_admin in emulator mode takes the unsigned token at its word.
    assert firebase_auth.verify_id_token(forged, app=emulator_app)['uid'] == 'victim-uid'

    assert install_self_hosted_id_token_guard() is True
    with pytest.raises(firebase_auth.InvalidIdTokenError):
        firebase_auth.verify_id_token(forged, app=emulator_app)
    with pytest.raises(firebase_auth.InvalidIdTokenError):
        firebase_auth.verify_id_token(local_session_token('owner-uid', secret='another-local-auth-secret-0123456789'))
    assert firebase_auth.verify_id_token(local_session_token('owner-uid'), check_revoked=True) == {'uid': 'owner-uid'}


@pytest.mark.parametrize(
    'environ',
    [
        {},
        # Upstream's dev harness: emulator, not self-hosted.
        {'FIREBASE_AUTH_EMULATOR_HOST': '127.0.0.1:9099'},
        # Self-hosted against a real Firebase project: signatures are checked.
        {'OMI_SELF_HOSTED': '1'},
        {'OMI_SELF_HOSTED': '1', 'FIREBASE_AUTH_EMULATOR_HOST': '  '},
    ],
)
def test_guard_stays_out_where_firebase_checks_signatures_or_no_one_self_hosts(environ):
    module = types.SimpleNamespace(InvalidIdTokenError=ValueError, verify_id_token=object())
    original = module.verify_id_token

    assert self_hosted_id_token_guard_enabled(environ) is False
    assert install_self_hosted_id_token_guard(environ, auth_module=module) is False
    assert module.verify_id_token is original


def test_production_verify_token_answers_a_forged_token_with_401(self_hosted_env, default_emulator_app, monkeypatch):
    from utils.other import endpoints

    monkeypatch.setattr(firebase_auth, 'verify_id_token', firebase_auth.verify_id_token)
    monkeypatch.delenv('ADMIN_KEY', raising=False)
    install_self_hosted_id_token_guard()

    with pytest.raises(HTTPException) as rejected:
        endpoints.get_current_user_uid(authorization=f'Bearer {forged_emulator_token("victim-uid")}')
    assert rejected.value.status_code == 401
    assert endpoints.verify_token(local_session_token('owner-uid')) == 'owner-uid'
