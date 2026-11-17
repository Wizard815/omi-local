"""omi-local approvals: utils/task_approval.py (tasks wait for the user's OK) and
utils/auto_approve.py (the optional, off-by-default auto-approve at a local time)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import approval_settings as approval_db
from routers import approvals as approvals_router
from utils import auto_approve, task_approval
from utils.other import endpoints as auth

NOW = datetime(2026, 11, 17, 21, 30, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch):
    tasks = {
        'old': {'id': 'old', 'description': 'From before approvals', 'completed': False},
        'heard': {
            'id': 'heard',
            'description': 'Call the plumber',
            'completed': False,
            'approval': 'pending',
            'created_at': NOW - timedelta(days=1),
            'due_at': NOW + timedelta(days=1),
        },
        'today': {
            'id': 'today',
            'description': 'Heard today',
            'completed': False,
            'approval': 'pending',
            'created_at': NOW - timedelta(hours=1),
        },
    }
    delivered = []
    monkeypatch.setattr(task_approval.action_items_db, 'get_action_item', lambda uid, tid: tasks.get(tid))
    monkeypatch.setattr(
        task_approval.action_items_db,
        'update_action_item',
        lambda uid, tid, data: tasks[tid].update(data) or True,
    )
    monkeypatch.setattr(
        task_approval.action_items_db,
        'get_action_items',
        lambda uid, completed=None, limit=None, **k: [dict(t) for t in tasks.values() if not t.get('completed')],
    )
    monkeypatch.setattr(task_approval, 'submit_with_context', lambda pool, fn, *a: delivered.append((fn.__name__, *a)))
    return tasks, delivered


def test_mark_extracted_new_tasks_wait_and_kept_ids_keep_their_answer(monkeypatch):
    monkeypatch.setenv('OMI_TASK_APPROVAL', 'on')
    items = [{'description': 'a'}, {'description': 'b'}, {'description': 'c'}]
    prior = [{'id': 'x', 'approval': 'approved'}, {'id': 'y'}]  # y: from before approvals = approved
    task_approval.mark_extracted(items, ['x', None, 'y'], prior)
    assert [i['approval'] for i in items] == ['approved', 'pending', 'approved']
    monkeypatch.setenv('OMI_TASK_APPROVAL', 'off')
    fresh = [{'description': 'd'}]
    task_approval.mark_extracted(fresh)
    assert 'approval' not in fresh[0]


def test_only_approved_or_unmarked_tasks_are_deliverable():
    assert task_approval.deliverable({}) and task_approval.deliverable({'approval': 'approved'})
    assert not task_approval.deliverable({'approval': 'pending'})
    assert not task_approval.deliverable({'approval': 'declined'})


def test_approve_with_edits_delivers_once_and_decline_is_undoable(store):
    tasks, delivered = store
    approved = task_approval.approve('u1', 'heard', description='Call the plumber about the leak')
    assert approved['approval'] == 'approved' and tasks['heard']['description'] == 'Call the plumber about the leak'
    assert delivered and delivered[0][0] == '_deliver'
    task_approval.approve('u1', 'heard')  # again: nothing delivered twice
    assert len(delivered) == 1
    # a waiting task declined, then the decline undone: it waits again
    assert task_approval.decline('u1', 'today')['approval'] == 'declined'
    assert tasks['today']['approval'] == 'declined'
    assert task_approval.decline('u1', 'today', declined=False)['approval'] == 'pending'
    # an approved task cannot be "declined" (delete it instead); a missing one is a 404
    with pytest.raises(task_approval.TaskApprovalError) as caught:
        task_approval.decline('u1', 'old')
    assert caught.value.status_code == 409
    with pytest.raises(task_approval.TaskApprovalError) as caught:
        task_approval.approve('u1', 'nope')
    assert caught.value.status_code == 404


def test_waiting_lists_only_pending_and_auto_approve_takes_only_past_days(store):
    tasks, delivered = store
    assert [t['id'] for t in task_approval.waiting('u1')] == ['heard', 'today']
    start_of_today = NOW.replace(hour=0, minute=0)
    assert task_approval.approve_waiting_before('u1', start_of_today) == ['heard']
    assert tasks['today']['approval'] == 'pending'


# ---- auto-approve ------------------------------------------------------------------------------


def test_auto_approve_is_off_by_default_and_runs_once_a_day_after_its_time(monkeypatch):
    local = datetime(2026, 11, 17, 21, 5)
    assert approval_db.DEFAULTS['auto_approve'] is False
    assert not auto_approve.is_due(dict(approval_db.DEFAULTS), local)
    on = {**approval_db.DEFAULTS, 'auto_approve': True, 'time': '21:00'}
    assert auto_approve.is_due(on, local)
    assert not auto_approve.is_due(on, local.replace(hour=20, minute=59))
    assert not auto_approve.is_due({**on, 'last_run_date': '2026-11-17'}, local)
    monkeypatch.setenv('OMI_AUTO_APPROVE', 'off')
    assert not auto_approve.is_due(on, local)


@pytest.fixture
def approving(monkeypatch):
    calls = {'tasks': [], 'days': [], 'recorded': []}
    settings = {**approval_db.DEFAULTS}
    monkeypatch.setattr(auto_approve.approval_db, 'get_settings', lambda uid: dict(settings))
    monkeypatch.setattr(auto_approve.approval_db, 'record_run', lambda uid, d: calls['recorded'].append(d))
    monkeypatch.setattr(auto_approve.notification_db, 'get_user_time_zone', lambda uid: 'Europe/Berlin')
    monkeypatch.setattr(
        auto_approve.task_approval,
        'approve_waiting_before',
        lambda uid, before: calls['tasks'].append(before) or ['heard'],
    )
    monkeypatch.setattr(auto_approve, 'approve_past_events', lambda uid, before: [])
    monkeypatch.setattr(
        auto_approve.day_review_service,
        'pending_dates',
        lambda uid, days=7: {'today': '2026-11-17', 'pending_dates': [], 'draft_dates': ['2026-11-16']},
    )
    monkeypatch.setattr(auto_approve.day_review_service, 'approve', lambda uid, d: calls['days'].append(d))
    return settings, calls


def test_auto_approve_does_nothing_unless_the_user_turned_it_on(approving):
    settings, calls = approving
    assert auto_approve.run_for_user('u1', now=NOW) == {}
    assert calls == {'tasks': [], 'days': [], 'recorded': []}


def test_auto_approve_at_the_users_local_time_approves_past_days(approving):
    settings, calls = approving
    settings.update(auto_approve=True, time='22:00')
    # 21:30 UTC is 22:30 in Berlin: due
    out = auto_approve.run_for_user('u1', now=NOW)
    assert out == {'tasks': ['heard'], 'events': [], 'days': ['2026-11-16']}
    # "before" is the start of the user's today (midnight Berlin = 23:00 UTC the day before)
    assert calls['tasks'] == [datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc)]
    assert calls['recorded'] == ['2026-11-17']
    # an hour earlier in Berlin it is not due yet
    settings.update(time='23:00')
    assert auto_approve.run_for_user('u1', now=NOW) == {}


# ---- the router ---------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, store):
    app = FastAPI()
    app.include_router(approvals_router.router)
    app.dependency_overrides[auth.get_current_user_uid] = lambda: 'u1'
    saved = {**approval_db.DEFAULTS}
    monkeypatch.setattr(approvals_router.approval_db, 'get_settings', lambda uid: dict(saved))
    monkeypatch.setattr(
        approvals_router.approval_db, 'set_settings', lambda uid, data: saved.update(data) or dict(saved)
    )

    async def inline(executor, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(approvals_router, 'run_blocking', inline)
    return TestClient(app), saved


def test_settings_default_off_and_can_be_turned_on_with_a_time(client):
    http, saved = client
    assert http.get('/v1/approvals/settings').json() == {
        'auto_approve': False,
        'time': '21:00',
        'operator_disabled': False,
    }
    assert http.put('/v1/approvals/settings', json={'auto_approve': True, 'time': '07:30'}).json()['auto_approve']
    assert saved['time'] == '07:30'
    assert http.put('/v1/approvals/settings', json={'time': '25:00'}).status_code == 422


def test_approve_and_decline_routes(client, store):
    http, _ = client
    tasks, _ = store
    body = http.post('/v1/approvals/tasks/heard/approve', json={'description': 'Call the plumber today'}).json()
    assert (
        body['ok']
        and body['task']['approval'] == 'approved'
        and body['task']['description'] == 'Call the plumber today'
    )
    assert http.post('/v1/approvals/tasks/today/decline', json={}).json()['task']['approval'] == 'declined'
    assert (
        http.post('/v1/approvals/tasks/today/decline', json={'declined': False}).json()['task']['approval'] == 'pending'
    )
    assert http.post('/v1/approvals/tasks/old/decline', json={}).json() == {
        'ok': False,
        'task': None,
        'error': 'This task is already approved; delete it instead',
    }
    assert http.post('/v1/approvals/tasks/nope/approve', json={}).status_code == 404


# ---- Approve all / Approve selected, and its Undo -------------------------------------------------


from models.family_calendar import FamilyCalendarEvent  # noqa: E402
from utils.caldav import sync as caldav_sync  # noqa: E402


def _event(cid, idx, heard_at):
    return FamilyCalendarEvent(
        conversation_id=cid, event_idx=idx, title=f'{cid}#{idx}', start=NOW + timedelta(days=2), heard_at=heard_at
    )


@pytest.fixture
def batch(monkeypatch, store):
    """Tasks from ``store``; heard events and day drafts as small in-memory fakes; one CalDAV mark counter."""
    tasks, delivered = store
    events = {
        ('c-yesterday', 0): {'approved': False, 'heard_at': NOW - timedelta(days=1), 'created': False},
        ('c-today', 0): {'approved': False, 'heard_at': NOW - timedelta(hours=2), 'created': False},
    }
    days = {'2026-11-16': 'draft'}
    marked = []

    def pending(uid):
        return [_event(c, i, e['heard_at']) for (c, i), e in events.items() if not e['approved']], []

    async def approve_event(uid, cid, idx, **_):
        events[(cid, idx)]['approved'] = True
        caldav_sync.mark_dirty(uid)

    def take_back_event(uid, cid, idx):
        event = events[(cid, idx)]
        if not event['approved']:
            return None
        if event['created']:
            return False
        event['approved'] = False
        caldav_sync.mark_dirty(uid)
        return True

    def approve_day(uid, d):
        if days.get(d) != 'draft':
            raise auto_approve.day_review_service.DayReviewError(409, 'This day is already approved')
        days[d] = 'approved'
        caldav_sync.mark_dirty(uid)

    def undo_day(uid, d):
        days[d] = 'draft'
        caldav_sync.mark_dirty(uid)

    async def inline(executor, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(auto_approve, 'run_blocking', inline)
    monkeypatch.setattr(auto_approve.family_calendar, 'pending', pending)
    monkeypatch.setattr(auto_approve.family_calendar, 'approve', approve_event)
    monkeypatch.setattr(auto_approve.family_calendar, 'take_back', take_back_event)
    monkeypatch.setattr(
        auto_approve.day_review_service,
        'pending_dates',
        lambda uid, days=7: {
            'today': '2026-11-17',
            'pending_dates': [],
            'draft_dates': sorted(d for d, s in DAYS.items() if s == 'draft'),
        },
    )
    DAYS = days
    monkeypatch.setattr(auto_approve.day_review_service, 'approve', approve_day)
    monkeypatch.setattr(auto_approve.day_review_service, 'undo_approval', undo_day)
    monkeypatch.setattr(auto_approve.notification_db, 'get_user_time_zone', lambda uid: 'UTC')
    monkeypatch.setattr(caldav_sync.redis_db.r, 'sadd', lambda key, uid: marked.append(uid), raising=False)
    return tasks, events, days, marked, delivered


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_approve_all_takes_every_waiting_task_event_and_summary_with_one_caldav_mark(batch):
    tasks, events, days, marked, delivered = batch
    out = _run(auto_approve.approve_batch('u1', all_waiting=True))
    assert out['task_ids'] == ['heard', 'today'] and out['task_count'] == 2
    assert [(e['conversation_id'], e['event_idx']) for e in out['events']] == [('c-yesterday', 0), ('c-today', 0)]
    assert out['day_dates'] == ['2026-11-16'] and out['day_count'] == 1
    assert tasks['heard']['approval'] == tasks['today']['approval'] == 'approved'
    assert days['2026-11-16'] == 'approved' and all(e['approved'] for e in events.values())
    assert marked == ['u1']  # one mark for the batch, not one per item
    assert len(delivered) == 2  # each task delivered once, as a tap on Approve would
    # idempotent: nothing waits any more
    again = _run(auto_approve.approve_batch('u1', all_waiting=True))
    assert again['task_count'] == again['event_count'] == again['day_count'] == 0 and len(delivered) == 2


def test_approve_selected_leaves_out_what_the_user_unticked(batch):
    tasks, events, days, marked, _ = batch
    out = _run(
        auto_approve.approve_batch(
            'u1', task_ids=['today', 'old'], events=[('c-today', 0), ('c-gone', 3)], day_dates=[]
        )
    )
    # 'old' was never waiting (made by the user) and c-gone is not a waiting event: both skipped
    assert out['task_ids'] == ['today'] and [e['conversation_id'] for e in out['events']] == ['c-today']
    assert out['day_dates'] == [] and days['2026-11-16'] == 'draft'  # the summary was unticked
    assert tasks['heard']['approval'] == 'pending' and not events[('c-yesterday', 0)]['approved']


def test_approve_all_of_one_day_is_scoped_to_that_local_day(batch):
    tasks, events, days, marked, _ = batch
    out = _run(auto_approve.approve_batch('u1', all_waiting=True, date_str='2026-11-16'))
    assert out['task_ids'] == ['heard'] and [e['conversation_id'] for e in out['events']] == ['c-yesterday']
    assert out['day_dates'] == ['2026-11-16']
    assert tasks['today']['approval'] == 'pending' and not events[('c-today', 0)]['approved']


def test_undo_takes_back_exactly_the_batch(batch):
    tasks, events, days, marked, _ = batch
    tasks['manual'] = {'id': 'manual', 'description': 'Mine', 'completed': False, 'approval': 'approved'}
    out = _run(auto_approve.approve_batch('u1', all_waiting=True))
    events[('c-today', 0)]['created'] = True  # it went to the family calendar: no recall there
    marked.clear()
    undone = auto_approve.undo_batch(
        'u1', out['task_ids'], [(e['conversation_id'], e['event_idx']) for e in out['events']], out['day_dates']
    )
    assert undone == {'task_count': 2, 'event_count': 1, 'day_count': 1, 'kept_count': 1}
    assert tasks['heard']['approval'] == tasks['today']['approval'] == 'pending'
    assert tasks['manual']['approval'] == 'approved'  # not in the batch: untouched
    assert days['2026-11-16'] == 'draft' and not events[('c-yesterday', 0)]['approved']
    assert marked == ['u1']
    # undoing twice changes nothing more
    assert auto_approve.undo_batch('u1', out['task_ids'], [], [])['task_count'] == 0


def test_take_back_calls_the_reminder_off(store, monkeypatch):
    tasks, _ = store
    recalled = []
    monkeypatch.setattr(task_approval, 'dispatch_action_item_reminder', lambda **kw: recalled.append(kw))
    task_approval.approve('u1', 'heard')
    assert task_approval.take_back('u1', 'heard') and tasks['heard']['approval'] == 'pending'
    assert recalled and recalled[0]['deleted'] is True
    assert not task_approval.take_back('u1', 'heard')  # waiting already


def test_batch_routes(client, batch):
    http, _ = client
    body = http.post('/v1/approvals/batch', json={'all_waiting': True, 'date': '2026-11-16'}).json()
    assert body['task_ids'] == ['heard'] and body['day_dates'] == ['2026-11-16']
    undone = http.post(
        '/v1/approvals/batch/undo',
        json={'task_ids': body['task_ids'], 'events': body['events'], 'day_dates': body['day_dates']},
    ).json()
    assert undone == {'task_count': 1, 'event_count': 1, 'day_count': 1, 'kept_count': 0}
    assert http.post('/v1/approvals/batch', json={'day_dates': ['16-11-2026']}).status_code == 422
    waiting = http.get('/v1/approvals')
    assert waiting.status_code == 200
