"""/_local/storage answers only signed URLs or the owner's login (utils/other/local_storage_gate.py)."""

import datetime
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.other import local_storage
from utils.other.local_storage import LocalStorageClient
from utils.other.local_storage_gate import LocalStorageGate

TOKENS = {'token-alice': 'alice', 'token-bob': 'bob'}


def _verify(token: str) -> str:
    if token not in TOKENS:
        raise ValueError('bad token')
    return TOKENS[token]


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / 'storage'
    monkeypatch.setenv('OMI_LOCAL_STORAGE_BASE_URL', 'http://testserver/_local/storage')
    monkeypatch.setenv('OMI_LOCAL_STORAGE_SIGNING_SECRET', 'test-signing-secret')
    client = LocalStorageClient(root)
    bucket = client.bucket('omi-memories-recordings')
    bucket.blob('alice/conv-1.wav').upload_from_string(b'alice-audio')
    bucket.blob('bob/conv-2.wav').upload_from_string(b'bob-audio')
    (tmp_path / 'secret.txt').write_text('outside')
    app = FastAPI()
    app.mount('/_local/storage', LocalStorageGate(root, verify_token=_verify))
    return TestClient(app), bucket


def _path(url: str) -> str:
    return url.removeprefix('http://testserver')


def test_no_token_is_401(setup):
    http, _ = setup
    response = http.get('/_local/storage/omi-memories-recordings/alice/conv-1.wav')
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'


def test_bad_token_is_401(setup):
    http, _ = setup
    response = http.get(
        '/_local/storage/omi-memories-recordings/alice/conv-1.wav', headers={'Authorization': 'Bearer forged'}
    )
    assert response.status_code == 401


def test_own_file_with_login_is_200_and_not_cacheable(setup):
    http, _ = setup
    response = http.get(
        '/_local/storage/omi-memories-recordings/alice/conv-1.wav', headers={'Authorization': 'Bearer token-alice'}
    )
    assert response.status_code == 200
    assert response.content == b'alice-audio'
    assert response.headers['cache-control'] == 'private, no-store'


def test_other_users_file_with_login_is_404(setup):
    http, _ = setup
    response = http.get(
        '/_local/storage/omi-memories-recordings/bob/conv-2.wav', headers={'Authorization': 'Bearer token-alice'}
    )
    assert response.status_code == 404
    assert b'bob-audio' not in response.content


def test_signed_url_works_without_login(setup):
    http, bucket = setup
    url = bucket.blob('alice/conv-1.wav').generate_signed_url(expiration=datetime.timedelta(minutes=60))
    response = http.get(_path(url))
    assert response.status_code == 200
    assert response.content == b'alice-audio'
    assert http.head(_path(url)).status_code == 200


def test_signature_is_bound_to_one_file(setup):
    http, bucket = setup
    url = bucket.blob('alice/conv-1.wav').generate_signed_url(expiration=datetime.timedelta(minutes=60))
    other = _path(url).replace('alice/conv-1.wav', 'bob/conv-2.wav')
    assert http.get(other).status_code == 403


def test_expired_or_forged_signature_is_403(setup, monkeypatch):
    http, bucket = setup
    url = bucket.blob('alice/conv-1.wav').generate_signed_url(expiration=datetime.timedelta(seconds=30))
    real_time = time.time
    monkeypatch.setattr(local_storage.time, 'time', lambda: real_time() + 3600)
    assert http.get(_path(url)).status_code == 403
    monkeypatch.setattr(local_storage.time, 'time', real_time)
    assert http.get(_path(url)[:-4] + '0000').status_code == 403


def test_signature_from_another_key_is_403(setup, monkeypatch):
    http, bucket = setup
    url = bucket.blob('alice/conv-1.wav').generate_signed_url()
    monkeypatch.setenv('OMI_LOCAL_STORAGE_SIGNING_SECRET', 'rotated')
    assert http.get(_path(url)).status_code == 403


@pytest.mark.parametrize(
    'path',
    [
        '/_local/storage/omi-memories-recordings/alice/../bob/conv-2.wav',
        '/_local/storage/omi-memories-recordings/alice/%2e%2e/bob/conv-2.wav',
        '/_local/storage/../secret.txt',
        '/_local/storage/omi-memories-recordings/..%2F..%2Fsecret.txt',
        '/_local/storage/.metadata/omi-memories-recordings/alice/conv-1.wav.json',
        '/_local/storage/omi-memories-recordings//alice/conv-1.wav',
    ],
)
def test_traversal_and_hidden_paths_are_refused(setup, path):
    http, _ = setup
    response = http.get(path, headers={'Authorization': 'Bearer token-alice'})
    assert response.status_code in {401, 403, 404}
    assert response.content not in {b'alice-audio', b'bob-audio', b'outside'}


def test_raw_traversal_scope_is_refused(setup):
    """Clients normalise ``..`` before sending; build the ASGI scope by hand to test the gate itself."""
    _, bucket = setup
    gate = LocalStorageGate(bucket.root, verify_token=_verify)
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': b''}

    async def send(message):
        sent.append(message)

    import asyncio

    for raw in ('/omi-memories-recordings/alice/../bob/conv-2.wav', '/../secret.txt', '/alice\\..\\bob'):
        sent.clear()
        scope = {
            'type': 'http',
            'method': 'GET',
            'path': raw,
            'root_path': '',
            'query_string': b'',
            'headers': [(b'authorization', b'Bearer token-alice')],
        }
        asyncio.run(gate(scope, receive, send))
        assert sent[0]['status'] == 404


def test_writes_are_refused(setup):
    http, _ = setup
    response = http.put(
        '/_local/storage/omi-memories-recordings/alice/conv-1.wav',
        content=b'x',
        headers={'Authorization': 'Bearer token-alice'},
    )
    assert response.status_code == 405


def test_public_url_is_signed_and_expires_within_a_week(setup):
    _, bucket = setup
    url = bucket.blob('alice/conv-1.wav').public_url
    exp = int(url.split('exp=')[1].split('&')[0])
    assert 0 < exp - time.time() <= 7 * 24 * 3600
