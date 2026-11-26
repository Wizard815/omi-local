"""utils/family_calendar.py: heard events wait for the user; approve writes one to dayGLANCE
through the ALLGlance gateway with the user's corrections; decline remembers "no"."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import httpx  # noqa: E402
import pytest  # noqa: E402

from utils import family_calendar as fc  # noqa: E402

NOW = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)


def _conversation(cid, events, title='Call with the landlord', started=NOW - timedelta(hours=2)):
    return {
        'id': cid,
        'started_at': started,
        'created_at': started,
        'structured': {'title': title, 'events': events},
    }


@pytest.fixture
def world(monkeypatch):
    store = {}
    saved = {}
    monkeypatch.setattr(fc.notifications_db, 'resolve_user_timezone', lambda uid: 'America/New_York')
    monkeypatch.setattr(fc.conversations_db, 'get_conversations', lambda uid, **kw: list(store.values()))
    monkeypatch.setattr(fc.conversations_db, 'get_conversation', lambda uid, cid: store.get(cid))
    monkeypatch.setattr(
        fc.conversations_db,
        'update_conversation_events',
        lambda uid, cid, events: (
            saved.__setitem__(cid, events),
            store[cid]['structured'].__setitem__('events', events),
        ),
    )
    integration = {'base_url': 'http://nas:8090/', 'access_token': 'gw-token'}
    monkeypatch.setattr(
        fc.users_db, 'get_task_integration', lambda uid, key: integration if key == 'allglance' else None
    )
    monkeypatch.setattr(fc.users_db, 'set_task_integration', lambda uid, key, data: integration.update(data))
    return store, saved


def test_pending_lists_unreviewed_future_events_and_keeps_reviewed_ones_apart(world):
    store, _ = world
    inspection = {'title': 'Inspection', 'start': NOW + timedelta(days=1), 'duration': 60}
    missed = {'title': 'Yesterday lunch', 'start': NOW - timedelta(days=1), 'duration': 30}
    added = {
        'title': 'Dentist',
        'start': NOW + timedelta(days=2),
        'duration': 30,
        'created': True,
        'calendar': 'allglance',
        'external_id': 'dayglance:7',
    }
    declined = {'title': 'Not real', 'start': NOW + timedelta(days=3), 'declined': True}
    store['c1'] = _conversation('c1', [inspection, missed, added, declined])
    store['c2'] = _conversation(
        'c2', [{'title': 'Movers', 'start': '2026-11-08T09:00:00', 'duration': 120}], title='Lunch with Dana'
    )
    waiting, recent = fc.pending('u1', now=NOW)
    assert [e.title for e in waiting] == ['Movers', 'Inspection'], 'oldest first; the missed one is not asked about'
    movers = waiting[0]
    assert movers.conversation_title == 'Lunch with Dana' and movers.event_idx == 0
    # a naive start is the user's local time (the extractor writes wall-clock times)
    assert movers.start == datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc)
    assert {e.title for e in recent} == {'Dentist', 'Not real'}
    assert all(not e.created for e in waiting)


def test_one_conversation_shows_all_its_events_even_past_ones_and_nothing_else(world):
    """The conversation page: every event heard there, the missed one included, none from elsewhere."""
    store, _ = world
    store['c1'] = _conversation(
        'c1',
        [
            {'title': 'Inspection', 'start': NOW + timedelta(days=1), 'duration': 60},
            {'title': 'Yesterday lunch', 'start': NOW - timedelta(days=1), 'duration': 30},
            {'title': 'Dentist', 'start': NOW + timedelta(days=2), 'created': True},
        ],
    )
    store['c2'] = _conversation('c2', [{'title': 'Movers', 'start': NOW + timedelta(days=1)}])
    waiting, recent = fc.pending('u1', now=NOW, conversation_id='c1')
    assert [e.title for e in waiting] == ['Yesterday lunch', 'Inspection']
    assert [e.title for e in recent] == ['Dentist']
    assert fc.pending('u1', now=NOW, conversation_id='gone') == ([], [])


def test_nothing_is_written_without_approve(world):
    """The ask-first contract: listing never touches the calendar or the stored events."""
    store, saved = world
    store['c1'] = _conversation('c1', [{'title': 'Inspection', 'start': NOW + timedelta(days=1)}])
    fc.pending('u1', now=NOW)
    assert saved == {}


def test_approve_sends_the_corrected_event_to_dayglance_and_remembers_it(world):
    store, saved = world
    store['c1'] = _conversation('c1', [{'title': 'Inspecton friday', 'start': NOW + timedelta(days=1), 'duration': 30}])
    posts = []

    def handler(request: httpx.Request):
        posts.append((str(request.url), request.headers.get('authorization'), request.read()))
        return httpx.Response(201, json={'source': 'dayglance', 'task': {'id': 'b42', 'title': 'Inspection'}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = asyncio.run(
        fc.approve(
            'u1',
            'c1',
            0,
            title='Inspection',
            start=datetime(2026, 11, 13, 11, 0, tzinfo=timezone(timedelta(hours=-5))),  # the user moved it
            duration=45,
            client=client,
        )
    )
    assert out.created and out.calendar == 'allglance' and out.external_id == 'dayglance:b42'
    url, auth, body = posts[0]
    assert url == 'http://nas:8090/api/allglance/tasks' and auth == 'Bearer gw-token'
    import json

    sent = json.loads(body)
    assert sent == {'title': 'Inspection', 'source': 'dayglance', 'start': '2026-11-13 11:00', 'duration_minutes': 45}
    stored = saved['c1'][0]
    assert stored['created'] and stored['calendar'] == 'allglance' and stored['title'] == 'Inspection'
    assert stored['start'] == datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc)
    # approving again does not create a second block
    assert asyncio.run(fc.approve('u1', 'c1', 0, client=client)).external_id == 'dayglance:b42' and len(posts) == 1


def test_approve_reports_a_gateway_refusal_and_keeps_the_event_pending(world):
    store, saved = world
    store['c1'] = _conversation('c1', [{'title': 'Inspection', 'start': NOW + timedelta(days=1)}])
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503, json={'error': 'dayGLANCE is off'}))
    )
    with pytest.raises(RuntimeError, match='503.*dayGLANCE is off'):
        asyncio.run(fc.approve('u1', 'c1', 0, client=client))
    assert saved == {}


def test_approve_without_a_family_calendar_means_add_to_my_caldav_calendar(world, monkeypatch):
    """No ALLGlance gateway: approve still works, records the yes (with the user's edits) and
    asks the CalDAV sync to put it on the user's own calendar; nothing is posted anywhere."""
    store, saved = world
    store['c1'] = _conversation('c1', [{'title': 'Inspection', 'start': NOW + timedelta(days=1)}])
    monkeypatch.setattr(fc.users_db, 'get_task_integration', lambda uid, key: None)
    dirty, remembered = [], []
    monkeypatch.setattr(fc.caldav_sync, 'mark_dirty', dirty.append)
    monkeypatch.setattr(fc.caldav_sync, 'remember_approved_event', lambda uid, cid: remembered.append(cid))
    assert fc.integration('u1') is None
    event = asyncio.run(fc.approve('u1', 'c1', 0, title='Home inspection', duration=45))
    assert event.approved and not event.created and not event.declined and event.title == 'Home inspection'
    stored = saved['c1'][0]
    assert stored['approved'] and not stored['created'] and stored['duration'] == 45
    assert dirty == ['u1'] and remembered == ['c1']
    # it moves from "waiting" to "reviewed lately"
    waiting, recent = fc.pending('u1', now=NOW)
    assert waiting == [] and [e.title for e in recent] == ['Home inspection']
    # declining afterwards takes the approval back (the sync then takes it off the calendar)
    declined = fc.decline('u1', 'c1', 0)
    assert declined.declined and not declined.approved and saved['c1'][0]['approved'] is False
    assert dirty == ['u1', 'u1']


def test_decline_marks_the_event_and_drops_it_from_the_queue(world):
    store, saved = world
    store['c1'] = _conversation('c1', [{'title': 'Inspection', 'start': NOW + timedelta(days=1)}])
    out = fc.decline('u1', 'c1', 0)
    assert out.declined and saved['c1'][0]['declined'] is True
    waiting, recent = fc.pending('u1', now=NOW)
    assert waiting == [] and recent[0].declined
    with pytest.raises(LookupError):
        fc.decline('u1', 'c1', 5)


def test_a_decline_can_be_taken_back_and_the_event_waits_again(world):
    store, saved = world
    store['c1'] = _conversation('c1', [{'title': 'Inspection', 'start': NOW + timedelta(days=1)}])
    fc.decline('u1', 'c1', 0)
    out = fc.decline('u1', 'c1', 0, declined=False)
    assert not out.declined and saved['c1'][0]['declined'] is False
    waiting, recent = fc.pending('u1', now=NOW)
    assert [e.title for e in waiting] == ['Inspection'] and recent == []


def test_bound_to_a_member_the_event_is_created_as_that_member(world):
    store, _ = world
    store['c1'] = _conversation('c1', [{'title': 'Inspection', 'start': NOW + timedelta(days=1), 'duration': 30}])
    fc.set_owner('u1', 'u-sam')
    assert fc.integration('u1')['owner_id'] == 'u-sam'
    sent = []

    def handler(request: httpx.Request):
        import json

        sent.append(json.loads(request.read()))
        return httpx.Response(201, json={'source': 'dayglance', 'task': {'id': 'b1'}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(fc.approve('u1', 'c1', 0, client=client))
    assert sent[0]['owner_id'] == 'u-sam'
    fc.set_owner('u1', None)
    assert fc.integration('u1')['owner_id'] is None


def test_members_come_from_the_gateway_roster_and_an_unreachable_gateway_means_none(world):
    ok = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={'users': [{'id': 'u-sam', 'name': 'Sam', 'source': 'dayglance'}, {'id': 7, 'name': ''}]}
            )
        )
    )
    target = fc.integration('u1')
    assert asyncio.run(fc.members(target, client=ok)) == [{'id': 'u-sam', 'name': 'Sam'}, {'id': '7', 'name': '7'}]
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError('down'))))
    assert asyncio.run(fc.members(target, client=down)) == []


# ---- today(): the Family day widget ----


def _gateway(day_body, chores_body=None, seen=None, roster=None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append((request.url.path, dict(request.url.params), request.headers.get('authorization')))
        if request.url.path.startswith('/api/allglance/days/'):
            return httpx.Response(200, json=day_body)
        if request.url.path == '/api/allglance/chores':
            return httpx.Response(200, json=chores_body or {'chores': []})
        if request.url.path == '/api/allglance/users':
            return httpx.Response(200, json={'users': roster or []})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_today_shows_what_is_still_ahead_tasks_due_and_overdue_chores(world):
    # NOW is 10:00 in New York on 2026-11-07
    seen = []
    day = {
        'date': '2026-11-07',
        'blocks': [
            {'title': 'Soccer', 'start_time': '15:00', 'duration_minutes': 90, 'owner_id': 'u-sam'},
            {'title': 'Breakfast', 'start_time': '08:00', 'duration_minutes': 30},  # over
            {'title': 'Standup', 'start_time': '09:45', 'duration_minutes': 30},  # still on
            {'title': 'Garbage day', 'all_day': True},
            {'title': 'Done thing', 'start_time': '18:00', 'completed': True},
            {'title': 'Dinner', 'start_time': '18:30', 'duration_minutes': 60},
        ],
        'tasks': {
            'vikunja': [{'title': 'Pay rent'}, {'title': 'Paid', 'completed': True}],
            'dayglance': [{'title': 'x'}],
        },
    }
    chores = {
        'chores': [
            {'title': 'Vacuum', 'overdue': True},
            {'title': 'Dishes', 'overdue': False},
            {'title': 'Never', 'overdue': None},
        ]
    }
    out = asyncio.run(fc.today('u1', now=NOW, client=_gateway(day, chores, seen)))
    assert out.configured and out.reachable and out.date == '2026-11-07' and out.error == ''
    assert [b.title for b in out.next] == ['Garbage day', 'Standup', 'Soccer'], 'all-day first, then soonest'
    assert out.remaining == 4
    standup = out.next[1]
    assert standup.time_label == '09:45' and standup.start == datetime(2026, 11, 7, 14, 45, tzinfo=timezone.utc)
    assert out.next[0].all_day and out.next[0].time_label == 'All day' and out.next[0].start is None
    assert out.tasks_due == 2 and out.chores_overdue == 1 and out.chores == ['Vacuum']
    assert seen[0] == ('/api/allglance/days/2026-11-07', {}, 'Bearer gw-token'), 'no owner: the whole family'


def test_today_reads_as_the_bound_member_and_names_them(world):
    fc.set_owner('u1', 'u-sam')
    seen = []
    client = _gateway({'blocks': [], 'tasks': {}}, seen=seen, roster=[{'id': 'u-sam', 'name': 'Sam'}])
    out = asyncio.run(fc.today('u1', now=NOW, client=client))
    assert out.owner_name == 'Sam' and out.next == [] and out.remaining == 0
    assert seen[0][1] == {'owner': 'u-sam'} and seen[1][1] == {'owner': 'u-sam'}


def test_today_without_a_gateway_or_with_it_down_says_so_instead_of_failing(world, monkeypatch):
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError('down'))))
    out = asyncio.run(fc.today('u1', now=NOW, client=down))
    assert out.configured and not out.reachable and "isn't answering" in out.error
    refused = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert 'refused' in asyncio.run(fc.today('u1', now=NOW, client=refused)).error
    monkeypatch.setattr(fc.users_db, 'get_task_integration', lambda uid, key: None)
    out = asyncio.run(fc.today('u1', now=NOW))
    assert not out.configured and not out.reachable
