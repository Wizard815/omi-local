"""Home Assistant (omi-local): reads within the operator's allowlist, who's home, service calls
that wait for the user's OK (or run when auto-approved, or are refused), the action log,
events with minimized payloads that never break their caller, and a token that never leaves.

Home Assistant is tests/unit/fixtures/home_assistant_fake.FakeHomeAssistant, a loopback
server answering the REST API's documented shapes."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402

import database.action_items as action_items_db  # noqa: E402
import database.day_bundle_users as active_users_db  # noqa: E402
import database.home_assistant as store  # noqa: E402
from models.conversation import Conversation  # noqa: E402
from routers import home_assistant as router_module  # noqa: E402
from tests.unit.fixtures.home_assistant_fake import TOKEN, FakeHomeAssistant  # noqa: E402
from tests.unit.fixtures.outbound_fakes import FakeFirestore  # noqa: E402
from utils import outbound_http, webhooks  # noqa: E402
from utils.home_assistant import actions, context, events, waiting  # noqa: E402
from utils.home_assistant import settings as ha_settings  # noqa: E402
from utils.mcp_server import registry  # noqa: E402
from utils.other import endpoints as auth  # noqa: E402
from utils.retrieval.tools import home_assistant_tools  # noqa: E402

UID = 'u1'
ENV = (
    'OMI_HA_URL',
    'OMI_HA_TOKEN',
    'OMI_HA_READ_ALLOW',
    'OMI_HA_ACTION_DOMAINS',
    'OMI_HA_AUTO_APPROVE',
    'OMI_HA_EVENTS',
    'OMI_HA_TIMEOUT_SECONDS',
)


@pytest.fixture
def ha(monkeypatch):
    fake = FakeHomeAssistant()
    db = FakeFirestore()
    monkeypatch.setattr(store, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(outbound_http, '_target', lambda url: (url, {'headers': {}, 'extensions': {}}))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('OMI_HA_URL', fake.url)
    monkeypatch.setenv('OMI_HA_TOKEN', TOKEN)
    zero = {'task_count': 0, 'event_count': 0, 'draft_dates': [], 'agent_jobs': []}
    monkeypatch.setattr(waiting.auto_approve, 'waiting_summary', lambda uid: dict(zero))
    monkeypatch.setattr(events.task_approval, 'approval_enabled', lambda: True)
    monkeypatch.setattr(actions.redis_db, 'r', FakeRedis())
    yield fake
    fake.close()


@pytest.fixture
def client(ha):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[auth.get_current_user_uid] = lambda: UID
    return TestClient(app)


class FakeRedis:
    def __init__(self):
        self.sets = {}

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    def spop(self, key, count=None):
        members = list(self.sets.pop(key, set()))
        return members


def run(coro):
    return asyncio.run(coro)


def service_calls(fake):
    return [r for r in fake.requests if r.path.startswith('/api/services/')]


# ---- reads -----------------------------------------------------------------------------------


def test_states_are_the_default_allowlist_only_and_compact(ha):
    out = run(context.get_states(UID))
    ids = [e['entity_id'] for e in out['entities']]
    assert 'light.living_room' in ids and 'climate.hallway' in ids and 'person.alex' in ids
    assert not {'lock.front_door', 'cover.garage', 'sensor.power', 'device_tracker.phone'} & set(ids)
    living = next(e for e in out['entities'] if e['entity_id'] == 'light.living_room')
    assert living == {'entity_id': 'light.living_room', 'name': 'Living room lights', 'state': 'on', 'brightness': 200}


def test_states_filter_by_domain_and_area_and_the_operator_list(ha, monkeypatch):
    assert [e['entity_id'] for e in run(context.get_states(UID, domain='light'))['entities']] == [
        'light.kitchen',
        'light.living_room',
    ]
    kitchen = run(context.get_states(UID, area='kitchen'))
    assert kitchen['area'] == 'Kitchen' and {e['entity_id'] for e in kitchen['entities']} == {
        'light.kitchen',
        'switch.coffee',
    }
    assert run(context.get_states(UID, area='attic'))['error'] == "No area named 'attic'"
    monkeypatch.setenv('OMI_HA_READ_ALLOW', 'sensor.power,lock')
    assert {e['entity_id'] for e in run(context.get_states(UID))['entities']} == {'sensor.power', 'lock.front_door'}


def test_whos_home_reads_people_and_needs_presence_allowed(ha, monkeypatch):
    out = run(context.whos_home(UID))
    assert out['home'] == ['Alex'] and out['away'] == ['Sam']
    monkeypatch.setenv('OMI_HA_READ_ALLOW', 'light')
    with pytest.raises(events.client.HomeAssistantError) as refused:
        run(context.whos_home(UID))
    assert refused.value.kind == 'refused'


def test_area_summary_counts_allowed_entities_lights_on_and_temperature(ha):
    out = run(context.area_summary(UID))
    rows = {r['area']: r for r in out['areas']}
    assert rows['Living Room'] == {'area': 'Living Room', 'entities': 1, 'lights_on': ['Living room lights']}
    assert rows['Hallway'] == {'area': 'Hallway', 'entities': 1, 'temperature': 20.5}  # the lock is not counted


# ---- actions -------------------------------------------------------------------------------


def test_a_light_call_waits_for_the_users_ok_then_runs_and_is_logged(ha, client, caplog):
    caplog.set_level(logging.INFO, logger='utils.home_assistant.actions')
    row = run(actions.request(UID, domain='light', service='turn_off', entity_id='light.living_room'))
    assert row['status'] == 'waiting_for_approval' and service_calls(ha) == []
    listed = client.get('/v1/home-assistant/actions').json()
    assert listed['waiting_count'] == 1 and listed['actions'][0]['id'] == row['id']

    answer = client.post(f"/v1/home-assistant/actions/{row['id']}/approve").json()
    assert answer['ok'] and answer['action']['status'] == 'executed' and answer['action']['decided_by'] == 'user'
    assert ha.services == [('light', 'turn_off', {'entity_id': 'light.living_room'})]
    assert ha.states['light.living_room']['state'] == 'off'
    logged = store.get_action(UID, row['id'])
    assert logged['status'] == 'executed' and logged['result_states'] == [
        {'entity_id': 'light.living_room', 'state': 'off'}
    ]
    lines = [r.getMessage() for r in caplog.records]
    assert any('call=light.turn_off light.living_room status=waiting_for_approval' in l for l in lines)
    assert any('status=executed by=user' in l for l in lines)
    again = client.post(f"/v1/home-assistant/actions/{row['id']}/approve").json()
    assert not again['ok'] and 'executed' in again['error']


def test_auto_approved_entities_run_at_once(ha, monkeypatch):
    monkeypatch.setenv('OMI_HA_AUTO_APPROVE', 'light.kitchen,scene')
    row = run(
        actions.request(UID, domain='light', service='turn_on', entity_id='light.kitchen', data={'brightness_pct': 40})
    )
    assert row['status'] == 'executed' and row['decided_by'] == 'auto'
    assert ha.services == [('light', 'turn_on', {'brightness_pct': 40, 'entity_id': 'light.kitchen'})]
    other = run(actions.request(UID, domain='light', service='turn_on', entity_id='light.living_room'))
    assert other['status'] == 'waiting_for_approval'


@pytest.mark.parametrize(
    'domain,service,entity,reason',
    [
        ('lock', 'unlock', 'lock.front_door', 'lock is not allowed'),
        ('cover', 'open_cover', 'cover.garage', 'cover is not allowed'),
        ('alarm_control_panel', 'alarm_disarm', 'alarm_control_panel.home', 'alarm_control_panel is not allowed'),
        ('switch', 'turn_on', 'switch.garage_door', 'switch.garage_door is not allowed'),
        ('homeassistant', 'restart', 'homeassistant.x', 'never callable'),
        ('sensor', 'turn_on', 'sensor.power', 'not in the allowed domains'),
        ('light', 'delete', 'light.kitchen', 'not an allowed service'),
        ('light', 'turn_off', 'switch.coffee', 'is not a light entity'),
        ('light', 'turn_off', 'all', 'is not a light entity'),
    ],
)
def test_denied_calls_are_refused_logged_and_never_reach_home_assistant(ha, domain, service, entity, reason):
    row = run(actions.request(UID, domain=domain, service=service, entity_id=entity))
    assert row['status'] == 'refused' and reason in row['error']
    assert service_calls(ha) == []
    assert store.get_action(UID, row['id'])['status'] == 'refused'


def test_targeting_fields_in_service_data_are_refused(ha):
    row = run(actions.request(UID, domain='light', service='turn_on', entity_id='light.kitchen', data={'area_id': 'x'}))
    assert row['status'] == 'refused' and 'area_id' in row['error']


def test_an_enabled_sensitive_domain_always_waits_even_when_auto_approved(ha, client, monkeypatch):
    monkeypatch.setenv('OMI_HA_ACTION_DOMAINS', 'light,lock,garage,switch')
    monkeypatch.setenv('OMI_HA_AUTO_APPROVE', 'lock,switch')
    unlock = run(actions.request(UID, domain='lock', service='unlock', entity_id='lock.front_door'))
    garage = run(actions.request(UID, domain='switch', service='turn_on', entity_id='switch.garage_door'))
    coffee = run(actions.request(UID, domain='switch', service='turn_on', entity_id='switch.coffee'))
    assert unlock['status'] == garage['status'] == 'waiting_for_approval' and unlock['sensitive']
    assert coffee['status'] == 'executed'
    declined = client.post(f"/v1/home-assistant/actions/{unlock['id']}/decline").json()
    assert declined['action']['status'] == 'declined'
    assert ('lock', 'unlock', {'entity_id': 'lock.front_door'}) not in ha.services


def test_approval_rechecks_the_rules_and_expires(ha, client, monkeypatch):
    monkeypatch.setenv('OMI_HA_ACTION_DOMAINS', 'light,lock')
    unlock = run(actions.request(UID, domain='lock', service='unlock', entity_id='lock.front_door'))
    monkeypatch.setenv('OMI_HA_ACTION_DOMAINS', 'light')  # the operator took locks back
    answer = client.post(f"/v1/home-assistant/actions/{unlock['id']}/approve").json()
    assert not answer['ok'] and 'lock is not allowed' in answer['error'] and service_calls(ha) == []

    old = run(actions.request(UID, domain='light', service='turn_off', entity_id='light.kitchen'))
    store.update_action(UID, old['id'], {'created_at': datetime.now(timezone.utc) - timedelta(hours=2)})
    expired = client.post(f"/v1/home-assistant/actions/{old['id']}/approve").json()
    assert not expired['ok'] and 'waited too long' in expired['error']
    assert client.post('/v1/home-assistant/actions/missing/approve').status_code == 404


def test_a_request_without_home_assistant_is_unconfigured(ha, monkeypatch):
    monkeypatch.delenv('OMI_HA_URL')
    with pytest.raises(events.client.HomeAssistantError) as error:
        run(actions.request(UID, domain='light', service='turn_off', entity_id='light.kitchen'))
    assert error.value.kind == 'unconfigured'
    assert run(home_assistant_tools.home_assistant_tools_for(UID)) == []


# ---- agent surfaces -------------------------------------------------------------------------


def test_mcp_tools_read_and_ask_through_the_registry(ha):
    states = registry.execute_tool(UID, 'ha_get_states', {'domain': 'climate'})
    assert [e['entity_id'] for e in states['entities']] == ['climate.hallway']
    asked = registry.execute_tool(
        UID,
        'ha_call_service',
        {'domain': 'light', 'service': 'turn_off', 'entity_id': 'light.kitchen', 'requested_by': 'Hermes'},
    )
    assert asked['status'] == 'waiting_for_approval' and asked['call'] == 'light.turn_off light.kitchen'
    assert store.get_action(UID, asked['action_id'])['requested_by'] == 'Hermes'
    refused = registry.execute_tool(
        UID, 'ha_call_service', {'domain': 'lock', 'service': 'unlock', 'entity_id': 'lock.front_door'}
    )
    assert refused['status'] == 'refused'
    assert registry.TOOLS_BY_NAME['ha_call_service'].scope == 'action_items.write'
    assert registry.TOOLS_BY_NAME['ha_whos_home'].annotations['readOnlyHint'] is True


def test_chat_tools_are_offered_when_connected_and_say_it_waits(ha):
    tools = run(home_assistant_tools.home_assistant_tools_for(UID))
    assert [t.name for t in tools] == ['ha_get_states', 'ha_whos_home', 'ha_area_summary', 'ha_call_service']
    config = RunnableConfig(configurable={'user_id': UID})
    text = run(
        home_assistant_tools.ha_call_service.ainvoke(
            {'domain': 'light', 'service': 'turn_off', 'entity_id': 'light.living_room'}, config=config
        )
    )
    assert 'waiting_for_approval' in text and 'NOT happened yet' in text
    who = json.loads(run(home_assistant_tools.ha_whos_home.ainvoke({}, config=config)))
    assert who['home'] == ['Alex']


# ---- events --------------------------------------------------------------------------------


def _conversation(**structured):
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
    return Conversation(
        id='conv-1',
        created_at=now,
        started_at=now,
        finished_at=now + timedelta(minutes=12),
        structured={
            'title': 'Garage shelves',
            'overview': 'Planned shelves for the garage. ' * 30,
            'category': 'other',
            'action_items': [{'description': 'Buy 3 boards', 'due_at': now + timedelta(days=1)}],
            **structured,
        },
        transcript_segments=[
            {'text': 'secret transcript words', 'speaker': 'SPEAKER_00', 'start': 0.0, 'end': 1.0, 'is_user': True}
        ],
        status='completed',
        language='en',
    )


def test_conversation_events_are_opt_in_and_minimized(ha):
    run(events.conversation_finished(UID, _conversation()))
    assert ha.events == []  # nothing chosen, no OMI_HA_EVENTS: nothing fired
    store.set_settings(UID, {'events': {'conversation_finished': True, 'task_created': True}, 'person': 'Alex'})
    run(events.conversation_finished(UID, _conversation()))
    (name, finished), (task_name, task) = ha.events
    assert name == 'omi_conversation_finished' and task_name == 'omi_task_created'
    assert finished['title'] == 'Garage shelves' and finished['person'] == 'Alex' and finished['task_count'] == 1
    assert len(finished['overview']) <= events.OVERVIEW_CHARS and finished['source'] == 'omi'
    assert task == {
        'conversation_id': 'conv-1',
        'due_at': '2026-11-26T09:00:00+00:00',
        'waiting_for_approval': True,
        'description': 'Buy 3 boards',
        'person': 'Alex',
        'source': 'omi',
    }
    assert 'secret transcript' not in json.dumps(ha.events)
    store.set_settings(UID, {'event_detail': 'minimal'})
    run(events.conversation_finished(UID, _conversation()))
    assert 'title' not in ha.events[-2][1] and 'description' not in ha.events[-1][1]


def test_home_assistant_down_never_breaks_conversation_processing(ha, monkeypatch):
    monkeypatch.setenv('OMI_HA_EVENTS', 'conversation_finished,task_created')
    monkeypatch.setenv('OMI_HA_URL', 'http://127.0.0.1:9')  # nothing listens
    fallbacks = []
    monkeypatch.setattr(events, 'record_fallback', lambda **kw: fallbacks.append(kw))
    monkeypatch.setattr(webhooks, 'user_webhook_status_db', lambda uid, kind: False)
    run(webhooks.conversation_created_webhook(UID, _conversation()))  # returns, no exception
    assert fallbacks and all(f['outcome'] == 'degraded' and f['from_mode'] == 'home_assistant' for f in fallbacks)
    assert fallbacks[0]['reason'] in ('connection_error', 'circuit_open')


def test_waiting_count_fires_within_seconds_and_only_when_it_goes_up(ha, client):
    store.set_settings(UID, {'events': {'waiting_for_approval': True}})
    first = run(actions.request(UID, domain='light', service='turn_off', entity_id='light.kitchen'))
    assert actions.redis_db.r.sets[actions.WAITING_DIRTY_KEY] == {UID}
    run(waiting.dirty_tick())
    run(waiting.dirty_tick())  # nothing marked: quiet
    run(waiting.waiting_changed(UID))  # same count again: quiet
    fired = [e for e in ha.events if e[0] == 'omi_waiting_for_approval']
    assert len(fired) == 1 and fired[0][1]['count'] == 1 and fired[0][1]['home_actions'] == 1
    client.post(f"/v1/home-assistant/actions/{first['id']}/decline")
    run(waiting.dirty_tick())  # down to 0: remembered, not announced
    run(actions.request(UID, domain='light', service='turn_off', entity_id='light.living_room'))
    run(waiting.dirty_tick())
    assert [e[1]['count'] for e in ha.events if e[0] == 'omi_waiting_for_approval'] == [1, 1]


def test_the_tick_announces_approved_tasks_that_came_due(ha, monkeypatch):
    now = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
    store.set_settings(UID, {'events': {'task_due': True}})
    store.set_state(UID, {'due_checked_at': now - timedelta(minutes=1)})
    due = now - timedelta(seconds=30)
    rows = [
        {'id': 't1', 'description': 'Take the bins out', 'due_at': due, 'approval': 'approved'},
        {'id': 't2', 'description': 'Waiting one', 'due_at': due, 'approval': 'pending'},
    ]
    monkeypatch.setattr(action_items_db, 'get_action_items', lambda uid, **kw: rows)
    monkeypatch.setattr(active_users_db, 'active_users', lambda: [UID])
    run(waiting.tick(now=now))
    assert ha.events == [
        (
            'omi_task_due',
            {
                'task_id': 't1',
                'due_at': due.isoformat(),
                'description': 'Take the bins out',
                'person': '',
                'source': 'omi',
            },
        )
    ]
    assert store.get_raw(UID)['due_checked_at'] == now
    run(waiting.tick(now=now))  # the same window again: nothing new
    assert len(ha.events) == 1


def test_day_summary_ready_event(ha):
    store.set_settings(UID, {'events': {'day_summary_ready': True}})
    run(events.day_summary_ready(UID, '2026-11-24', 'Shelves and a birthday'))
    assert ha.events == [
        (
            'omi_day_summary_ready',
            {'date': '2026-11-24', 'title': 'Shelves and a birthday', 'person': '', 'source': 'omi'},
        )
    ]


# ---- settings and the token ------------------------------------------------------------------


def test_the_token_never_leaves_the_server(ha, client, monkeypatch):
    shown = client.get('/v1/home-assistant/settings').json()
    assert shown['configured'] and shown['has_token'] and shown['token_from_env']
    own = 'own-user-token-77a1'
    saved = client.put(
        '/v1/home-assistant/settings', json={'token': own, 'person': 'Alex', 'events': {'task_due': True}}
    ).json()
    assert saved['has_token'] and not saved['token_from_env'] and saved['events']['task_due']
    assert store.get_raw(UID)['token_enc'] and own not in json.dumps(store.get_raw(UID), default=str)
    probe = client.post('/v1/home-assistant/test').json()  # the fake only knows TOKEN: refused
    assert probe == {'ok': False, 'detail': '', 'error': 'Home Assistant refused the access token'}
    client.put('/v1/home-assistant/settings', json={'token': ''})
    assert client.post('/v1/home-assistant/test').json()['ok']
    # Another address without its own token never gets the stack's token.
    moved = client.put('/v1/home-assistant/settings', json={'url': 'http://192.0.2.99:8123'}).json()
    assert not moved['has_token'] and not moved['configured']
    assert client.put('/v1/home-assistant/settings', json={'url': 'ftp://x'}).status_code == 422
    client.delete('/v1/home-assistant/settings')
    run(actions.request(UID, domain='light', service='turn_off', entity_id='light.kitchen'))
    bodies = [
        client.get('/v1/home-assistant/settings').text,
        client.get('/v1/home-assistant/actions').text,
        json.dumps(registry.execute_tool(UID, 'ha_get_states', {}), default=str),
        json.dumps(registry.execute_tool(UID, 'ha_whos_home', {}), default=str),
    ]
    for body in bodies:
        assert TOKEN not in body and own not in body
    assert all(r.headers.get('Authorization') == f'Bearer {TOKEN}' for r in ha.requests[-2:])


def test_events_default_from_the_stack_and_settings_validate(ha, monkeypatch):
    monkeypatch.setenv('OMI_HA_EVENTS', 'waiting_for_approval,bogus')
    effective = ha_settings.effective(UID)
    assert effective['events'] == {
        'conversation_finished': False,
        'task_created': False,
        'task_due': False,
        'waiting_for_approval': True,
        'day_summary_ready': False,
    }
    store.set_settings(UID, {'events': {'waiting_for_approval': False}})
    assert not ha_settings.effective(UID)['events']['waiting_for_approval']
