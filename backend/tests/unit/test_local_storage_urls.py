"""The /_local/storage mount serves only links the server signed (SELF_HOSTING_ROADMAP open item 1)."""

import datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.other.local_storage import LocalStorageClient
from utils.other.local_storage_urls import SignedStaticFiles, reset_url_key_cache


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    reset_url_key_cache()
    state_root = tmp_path / 'harness'
    storage_root = state_root / 'services' / 'storage'
    for name, value in {
        'OMI_HARNESS_STATE_ROOT': str(state_root),
        'OMI_LOCAL_STORAGE_ROOT': str(storage_root),
        'OMI_LOCAL_STORAGE_BASE_URL': 'http://testserver/_local/storage',
        'FIREBASE_PROJECT_ID': 'demo-omi-local',
        'FIRESTORE_EMULATOR_HOST': '127.0.0.1:8085',
    }.items():
        monkeypatch.setenv(name, value)
    for name in ('OMI_LOCAL_STORAGE_URL_KEY', 'OMI_LOCAL_STORAGE_ALLOW_UNSIGNED', 'BUCKET_APP_THUMBNAILS'):
        monkeypatch.delenv(name, raising=False)
    client = LocalStorageClient.from_env()
    app = FastAPI()
    app.mount('/_local/storage', SignedStaticFiles(directory=storage_root), name='local-storage')
    yield client, TestClient(app), state_root
    reset_url_key_cache()


def _path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f'?{parts.query}' if parts.query else '')


def test_local_storage_signed_link_is_served(store) -> None:
    client, http, _ = store
    blob = client.bucket('recordings').blob('uid-1/conv-1.wav')
    blob.upload_from_string(b'audio')

    response = http.get(_path_and_query(blob.public_url))

    assert response.status_code == 200
    assert response.content == b'audio'


def test_local_storage_unsigned_or_tampered_link_is_refused(store) -> None:
    client, http, _ = store
    client.bucket('recordings').blob('uid-1/conv-1.wav').upload_from_string(b'audio')
    client.bucket('recordings').blob('uid-2/conv-2.wav').upload_from_string(b'other')
    good = _path_and_query(client.bucket('recordings').blob('uid-1/conv-1.wav').public_url)

    assert http.get('/_local/storage/recordings/uid-1/conv-1.wav').status_code == 403
    assert http.get(good.replace('uid-1/conv-1', 'uid-2/conv-2')).status_code == 403
    assert http.get(good[:-1] + ('0' if good[-1] != '0' else '1')).status_code == 403
    # Adapter metadata lives under the same root and is never linked.
    assert http.get('/_local/storage/.metadata/recordings/uid-1/conv-1.wav.json').status_code == 403


def test_local_storage_expired_link_is_refused(store) -> None:
    client, http, _ = store
    blob = client.bucket('recordings').blob('uid-1/conv-1.wav')
    blob.upload_from_string(b'audio')

    fresh = blob.generate_signed_url(version='v4', expiration=datetime.timedelta(minutes=5), method='GET')
    stale = blob.generate_signed_url(expiration=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))

    assert 'exp=' in fresh
    assert http.get(_path_and_query(fresh)).status_code == 200
    assert http.get(_path_and_query(stale)).status_code == 403
    # Moving the expiry forward breaks the signature.
    assert http.get(_path_and_query(stale).replace('exp=1577836800', 'exp=4102444800')).status_code == 403


def test_local_storage_public_app_art_stays_unsigned(store, monkeypatch: pytest.MonkeyPatch) -> None:
    client, http, _ = store
    monkeypatch.setenv('BUCKET_APP_THUMBNAILS', 'app-thumbnails')
    blob = client.bucket('app-thumbnails').blob('thumb-1.jpg')
    blob.upload_from_string(b'jpeg')

    assert '?' not in blob.public_url
    assert http.get('/_local/storage/app-thumbnails/thumb-1.jpg').status_code == 200


def test_local_storage_allow_unsigned_keeps_old_links_working(store, monkeypatch: pytest.MonkeyPatch) -> None:
    client, http, _ = store
    client.bucket('recordings').blob('uid-1/conv-1.wav').upload_from_string(b'audio')
    monkeypatch.setenv('OMI_LOCAL_STORAGE_ALLOW_UNSIGNED', '1')

    assert http.get('/_local/storage/recordings/uid-1/conv-1.wav').status_code == 200


def test_local_storage_key_persists_and_is_private(store) -> None:
    client, _, state_root = store
    blob = client.bucket('recordings').blob('uid-1/conv-1.wav')
    first = blob.public_url
    key_file = state_root / 'secrets' / 'local_storage_url_key'

    reset_url_key_cache()

    assert blob.public_url == first
    assert key_file.stat().st_mode & 0o077 == 0
    assert len(key_file.read_text().strip()) == 64
