"""routers/caldav.py (omi-local): the settings the app shows on the shared bundled server, the
collections picker (GET/POST /v1/caldav/collections) and the task mode."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database.caldav_settings as settings_db
from routers import caldav as caldav_router
from utils.caldav import sync
from utils.other import endpoints as auth


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv('CALDAV_DEFAULT_URL', 'http://omi-radicale:5232/omi/')
    monkeypatch.setenv('CALDAV_DEFAULT_USERNAME', 'omi')
    monkeypatch.setenv('CALDAV_DEFAULT_PASSWORD', 'pw')
    stored = {}
    dirty = []
    monkeypatch.setattr(caldav_router.settings_db, 'get_settings', lambda uid: dict(stored) if stored else None)

    def set_settings(uid, data, password=None):
        stored.update(
            {**settings_db.DEFAULTS, **stored, **{k: v for k, v in data.items() if k in settings_db.DEFAULTS}}
        )
        return stored

    monkeypatch.setattr(caldav_router.settings_db, 'set_settings', set_settings)
    monkeypatch.setattr(sync.settings_db, 'get_settings', lambda uid: dict(stored) if stored else None)
    monkeypatch.setattr(caldav_router.active_users_db, 'mark_active', lambda uid: None)
    monkeypatch.setattr(sync, 'owner_label', lambda uid: 'Alice')
    monkeypatch.setattr(sync, 'mark_dirty', dirty.append)

    async def inline(executor, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(caldav_router, 'run_blocking', inline)
    app = FastAPI()
    app.include_router(caldav_router.router)
    app.dependency_overrides[auth.get_current_user_uid] = lambda: 'u1'
    return TestClient(app), stored, dirty


def test_the_bundled_server_shows_the_users_own_choices_and_suggested_names(http):
    client, stored, dirty = http
    body = client.get('/v1/caldav/settings').json()
    assert body['bundled'] and body['shared_account'] and body['task_mode'] == 'list'
    assert body['suggested_calendar_name'] == 'Omi – Alice' and body['suggested_tasks_name'] == 'Omi Tasks – Alice'
    body = client.put(
        '/v1/caldav/settings', json={'tasks_href': '/omi/family/', 'task_mode': 'parent', 'pull_events': False}
    ).json()
    assert body['bundled'] and body['tasks_href'] == '/omi/family/' and body['task_mode'] == 'parent'
    assert body['pull_events'] is False and dirty == ['u1']
    assert client.put('/v1/caldav/settings', json={'task_mode': 'nested'}).status_code == 422


def test_collections_are_listed_and_a_new_one_becomes_mine(http, monkeypatch):
    client, stored, dirty = http
    listed = [
        {'href': '/omi/omi-alice/', 'name': 'Omi – Alice', 'components': ['VEVENT'], 'used_by': [], 'mine': []},
        {'href': '/omi/omi-bob/', 'name': 'Omi – Bob', 'components': ['VEVENT'], 'used_by': ['Bob'], 'mine': []},
    ]
    monkeypatch.setattr(caldav_router.caldav_sync, 'list_collections', lambda uid, settings: listed)
    body = client.get('/v1/caldav/collections').json()
    assert body['ok'] and [c['used_by'] for c in body['collections']] == [[], ['Bob']]
    monkeypatch.setattr(
        caldav_router.caldav_sync,
        'create_collection',
        lambda uid, settings, name, kind: {'href': '/omi/alices-list/', 'name': name, 'components': ['VTODO']},
    )
    body = client.post('/v1/caldav/collections', json={'name': "Alice's list", 'kind': 'tasks'}).json()
    assert (
        body['ok'] and body['collection']['mine'] == ['tasks'] and body['settings']['tasks_href'] == '/omi/alices-list/'
    )
    assert dirty == ['u1']
