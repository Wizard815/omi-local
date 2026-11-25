"""omi-local: what an agent writes over MCP waits for the user's OK.

- create_calendar_event stores the event as a heard event of an agent-request conversation,
  so it waits in the Calendar check and reaches CalDAV only once approved;
- get_calendar_events reads the CalDAV snapshot plus Omi's events, each with its state;
- create_action_item over MCP writes a waiting task (no reminder until approved);
- the day review leaves agent requests out of the day.
"""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402

from models.family_calendar import FamilyCalendarEvent  # noqa: E402
from utils import agent_calendar, day_review_service, mcp_action_items, task_approval  # noqa: E402
from utils.mcp_server.errors import ToolExecutionError  # noqa: E402
from utils.mcp_server.handlers import calendar as calendar_handlers  # noqa: E402
from utils.mcp_server.registry import TOOL_REQUIRED_SCOPE, spec_for_tool  # noqa: E402

NOW = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)


@pytest.fixture
def stored(monkeypatch):
    rows = []
    monkeypatch.setattr(
        agent_calendar.conversations_db, 'upsert_conversation_with_lifecycle', lambda uid, data: rows.append(data)
    )
    return rows


def test_proposed_event_waits_as_an_agent_request(stored):
    event = agent_calendar.propose_event(
        'u1', title='Dentist', start='2026-11-26T16:00:00-05:00', duration=45, agent='Hermes', now=NOW
    )
    assert event['status'] == 'waiting_for_approval' and event['event_idx'] == 0
    (row,) = stored
    assert row['agent_request']['agent'] == 'Hermes' and row['status'] == 'completed'
    (heard,) = row['structured']['events']
    assert heard['start'] == datetime(2026, 11, 26, 21, tzinfo=timezone.utc)
    assert heard['duration'] == 45 and not heard.get('approved') and not heard.get('created')
    assert row['structured']['title'] == 'Hermes asked to add: Dentist'


@pytest.mark.parametrize(
    'kwargs',
    [
        {'title': '', 'start': '2026-11-26T16:00:00Z'},
        {'title': 'x', 'start': '2026-11-26T16:00:00'},  # no offset: never guess the zone
        {'title': 'x', 'start': 'thursday'},
        {'title': 'x', 'start': '2026-11-01T16:00:00Z'},  # past
        {'title': 'x', 'start': '2026-11-26T16:00:00Z', 'duration': 1},
    ],
)
def test_bad_requests_are_refused(stored, kwargs):
    with pytest.raises(agent_calendar.AgentCalendarError):
        agent_calendar.propose_event('u1', now=NOW, **kwargs)
    assert stored == []


def test_list_events_merges_calendar_and_omi_states(monkeypatch):
    monkeypatch.setattr(
        agent_calendar.device_calendar_db,
        'get_events',
        lambda uid, a, b: [
            {'title': 'Soccer', 'start': '2026-11-25T22:00:00+00:00', 'end': '2026-11-25T23:00:00+00:00'}
        ],
    )

    def heard(title, hours, **state):
        return FamilyCalendarEvent(
            conversation_id='c-' + title, event_idx=0, title=title, start=NOW + timedelta(hours=hours), **state
        )

    monkeypatch.setattr(
        agent_calendar.family_calendar,
        'pending',
        lambda uid: ([heard('Dentist', 30)], [heard('Plumber', 20, approved=True), heard('Old', -100, declined=True)]),
    )
    events = agent_calendar.list_events('u1', NOW, NOW + timedelta(days=7))
    assert [(e['title'], e['status']) for e in events] == [
        ('Plumber', 'on_calendar'),
        ('Dentist', 'waiting_for_approval'),
        ('Soccer', 'on_calendar'),
    ]


def test_mcp_calendar_tools_scopes_and_errors(monkeypatch, stored):
    assert TOOL_REQUIRED_SCOPE['get_calendar_events'] == 'conversations.read'
    assert TOOL_REQUIRED_SCOPE['create_calendar_event'] == 'action_items.write'
    assert spec_for_tool('get_calendar_events').annotations['readOnlyHint'] is True
    with pytest.raises(ToolExecutionError):
        calendar_handlers.create_calendar_event('u1', {'title': 'x', 'start': 'soon'})
    out = calendar_handlers.create_calendar_event(
        'u1',
        {
            'title': 'Dentist',
            'start': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            'requested_by': 'Hermes',
        },
    )
    assert out['event']['status'] == 'waiting_for_approval' and 'OK' in out['message']


def test_mcp_task_waits_for_approval_without_a_reminder(monkeypatch):
    created = {}
    reminders = []
    monkeypatch.setenv('OMI_TASK_APPROVAL', 'on')
    monkeypatch.delenv('OMI_MCP_TASK_APPROVAL', raising=False)

    def create(uid, data, **kwargs):
        created.update(data, id='t1')
        return 't1'

    monkeypatch.setattr(mcp_action_items.action_items_db, 'create_action_item', create)
    monkeypatch.setattr(mcp_action_items.action_items_db, 'get_action_item', lambda uid, tid: dict(created))
    monkeypatch.setattr(mcp_action_items, 'upsert_action_item_vector', lambda *a: None)
    monkeypatch.setattr(mcp_action_items, 'sync_action_item_reminder', lambda **k: reminders.append(k))

    item = mcp_action_items.create_action_item('u1', 'Call State Farm', due_at='2026-11-27T15:00:00Z')
    assert created['approval'] == task_approval.PENDING and item['approval'] == 'pending'
    assert reminders == []

    monkeypatch.setenv('OMI_MCP_TASK_APPROVAL', 'off')
    created.clear()
    item = mcp_action_items.create_action_item('u1', 'Call State Farm', due_at='2026-11-27T15:00:00Z')
    assert 'approval' not in created and len(reminders) == 1


def test_day_review_leaves_agent_requests_out(monkeypatch):
    monkeypatch.setattr(
        day_review_service.conversations_db,
        'get_conversations',
        lambda *a, **k: [{'id': 'req', 'agent_request': {'agent': 'Hermes'}, 'status': 'completed'}],
    )
    monkeypatch.setattr(day_review_service.day_reviews_db, 'get_day_review', lambda uid, date: None)
    loaded = day_review_service.load_day('u1', NOW.date(), 'UTC')
    assert loaded.visible == [] and loaded.rolled_up == [] and loaded.dropped_auto_count == 0
