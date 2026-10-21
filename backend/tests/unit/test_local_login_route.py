"""POST /v1/auth/local-login: omi-local's username/password sign-in.

The handler is a plain `def`: authenticate_local_account does argon2 hashing
plus Firestore and Redis reads, so FastAPI must run it in its threadpool rather
than on the event loop.
"""

import importlib
import inspect

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def auth_module():
    return importlib.import_module("routers.auth")


@pytest.fixture
def client(auth_module):
    app = FastAPI()
    app.include_router(auth_module.router)
    return TestClient(app)


def test_handler_runs_off_the_event_loop(auth_module):
    assert not inspect.iscoroutinefunction(auth_module.local_login)


def test_valid_credentials_return_a_session_token(auth_module, client, monkeypatch):
    calls = []

    def _authenticate(username, password, *, client_ip):
        calls.append((username, password, client_ip))
        return "session-token"

    monkeypatch.setattr(auth_module, "authenticate_local_account", _authenticate)
    response = client.post("/v1/auth/local-login", json={"username": "owner", "password": "pw"})

    assert response.status_code == 200
    assert response.json() == {"session_token": "session-token"}
    assert calls == [("owner", "pw", "testclient")]


def test_rejected_credentials_keep_their_status(auth_module, client, monkeypatch):
    def _reject(username, password, *, client_ip):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    monkeypatch.setattr(auth_module, "authenticate_local_account", _reject)
    response = client.post("/v1/auth/local-login", json={"username": "owner", "password": "wrong"})

    assert response.status_code == 401
