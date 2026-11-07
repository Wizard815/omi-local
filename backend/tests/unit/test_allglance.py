"""utils/allglance.py: the bundled ALLGlance gateway becomes a chat app and the default task
integration for every active member, idempotently, from the stack's env."""

import os

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402

from utils import allglance  # noqa: E402

SETTINGS = {'url': 'http://nas:8090', 'mcp_url': 'http://nas:8090/mcp', 'token': 'gw-token'}
TOOLS = [{'name': 'allglance_get_day', 'description': 'd', 'endpoint': 'http://nas:8090/mcp', 'is_mcp': True}]


@pytest.fixture
def world(monkeypatch):
    apps, enabled, integrations, default = {}, set(), {}, {'value': None}
    monkeypatch.setattr(allglance.apps_db, 'get_app_by_id_db', lambda app_id: apps.get(app_id))
    monkeypatch.setattr(allglance.apps_db, 'add_app_to_db', lambda data: apps.__setitem__(data['id'], dict(data)))
    monkeypatch.setattr(allglance.apps_db, 'update_app_in_db', lambda data: apps.__setitem__(data['id'], dict(data)))
    monkeypatch.setattr(allglance.redis_db, 'enable_app', lambda uid, app_id: enabled.add((uid, app_id)) or True)
    monkeypatch.setattr(allglance.users_db, 'get_user_profile', lambda uid: {'display_name': 'Sam', 'email': 's@x'})
    monkeypatch.setattr(allglance.users_db, 'get_task_integration', lambda uid, key: integrations.get((uid, key)))
    monkeypatch.setattr(
        allglance.users_db,
        'set_task_integration',
        lambda uid, key, data: integrations.setdefault((uid, key), {}).update(data),
    )
    monkeypatch.setattr(allglance.users_db, 'get_default_task_integration', lambda uid: default['value'])
    monkeypatch.setattr(allglance.users_db, 'set_default_task_integration', lambda uid, key: default.update(value=key))
    return apps, enabled, integrations, default


def test_settings_come_from_env(monkeypatch):
    monkeypatch.delenv('ALLGLANCE_URL', raising=False)
    assert allglance.settings_from_env() is None
    monkeypatch.setenv('ALLGLANCE_URL', 'http://nas:8090/')
    monkeypatch.setenv('ALLGLANCE_TOKEN', 'gw-token')
    assert allglance.settings_from_env() == SETTINGS


def test_chat_app_is_created_once_enabled_and_kept_current(world):
    apps, enabled, _, _ = world
    assert allglance.ensure_chat_app('u1', SETTINGS, TOOLS) is True
    app = apps['allglance-u1']
    assert app['private'] and app['capabilities'] == ['chat'] and app['chat_tools'] == TOOLS
    assert app['external_integration'] == {
        'mcp_server_url': 'http://nas:8090/mcp',
        'mcp_oauth_tokens': {'access_token': 'gw-token', 'token_type': 'static'},
    }
    assert ('u1', 'allglance-u1') in enabled
    assert allglance.ensure_chat_app('u1', SETTINGS, TOOLS) is False, 'second run creates nothing'
    # the stack's token rotated: the app follows without being recreated
    assert allglance.ensure_chat_app('u1', {**SETTINGS, 'token': 'new'}, TOOLS) is False
    assert apps['allglance-u1']['external_integration']['mcp_oauth_tokens']['access_token'] == 'new'
    assert len(apps) == 1


def test_task_integration_is_seeded_and_made_default_but_never_overrides_the_users_own(world):
    _, _, integrations, default = world
    assert allglance.ensure_task_integration('u1', SETTINGS) is True
    assert integrations[('u1', 'allglance')] == {
        'connected': True,
        'access_token': 'gw-token',
        'base_url': 'http://nas:8090',
        'source': 'vikunja',
        'seeded_from_env': True,
    }
    assert default['value'] == 'allglance'
    # the user picked another default: it stays
    default['value'] = 'todoist'
    assert allglance.ensure_task_integration('u1', SETTINGS) is False
    assert default['value'] == 'todoist'
    # a hand-saved integration (no seeded flag) is left alone even when env changes
    integrations[('u1', 'allglance')] = {'connected': True, 'access_token': 'mine', 'base_url': 'http://other'}
    allglance.ensure_task_integration('u1', {**SETTINGS, 'token': 'rotated'})
    assert integrations[('u1', 'allglance')]['access_token'] == 'mine'


@pytest.mark.asyncio
async def test_ensure_for_user_wires_both_and_tolerates_a_dark_gateway(world, monkeypatch):
    apps, _, integrations, _ = world

    async def discover(settings, *, now):
        return TOOLS

    monkeypatch.setattr(allglance, '_discover_tools', discover)
    outcome = await allglance.ensure_for_user('u1', SETTINGS)
    assert outcome == {'configured': True, 'chat_app': True, 'task_integration': True, 'tools': 1}

    async def dark(settings, *, now):
        return None

    monkeypatch.setattr(allglance, '_discover_tools', dark)
    outcome = await allglance.ensure_for_user('u2', SETTINGS)
    assert outcome['chat_app'] is False and outcome['task_integration'] is True, 'tasks still wire without the MCP'
    assert 'allglance-u2' not in apps and ('u2', 'allglance') in integrations
    assert await allglance.ensure_for_user('u3', None) == {'configured': False} or os.getenv('ALLGLANCE_URL')
