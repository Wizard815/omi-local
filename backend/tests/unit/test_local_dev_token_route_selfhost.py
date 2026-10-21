"""omi-local keeps upstream's password-less local-dev token route switched off.

routers/auth.py enables POST /v1/auth/local-dev/custom-token whenever the
Firebase Auth emulator is configured. An omi-local stack always is (the emulator
is its real auth store), so without the opt-out any caller who can reach the
backend could mint a token for any uid. omi-host/entrypoint.sh sets
OMI_DISABLE_LOCAL_DEV_TOKEN_ROUTE=1.
"""

import importlib

import pytest
from fastapi import HTTPException


@pytest.fixture
def auth_module():
    return importlib.import_module("routers.auth")


def test_self_hosted_opt_out_wins_over_the_emulator_host(auth_module, monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
    assert auth_module.local_dev_auth_enabled() is True

    monkeypatch.setenv("OMI_DISABLE_LOCAL_DEV_TOKEN_ROUTE", "1")
    assert auth_module.local_dev_auth_enabled() is False


@pytest.mark.asyncio
async def test_opted_out_route_answers_404_and_mints_nothing(auth_module, monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
    monkeypatch.setenv("OMI_DISABLE_LOCAL_DEV_TOKEN_ROUTE", "true")
    minted = []
    monkeypatch.setattr(auth_module.firebase_admin.auth, "create_custom_token", lambda uid: minted.append(uid))

    with pytest.raises(HTTPException) as excinfo:
        await auth_module.local_dev_custom_token(uid="victim-uid", email=None)

    assert excinfo.value.status_code == 404
    assert minted == []
