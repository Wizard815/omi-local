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
    import utils.executors as executors

    monkeypatch.setattr(executors, 'submit_with_context', lambda pool, fn, *a: delivered.append((fn.__name__, *a)))
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
