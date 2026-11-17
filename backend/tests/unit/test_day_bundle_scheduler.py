"""utils/day_bundle_scheduler.py: which days are due, and one user's failure never stops the others."""

from datetime import datetime

import pytest

from utils import day_bundle_scheduler as scheduler
from utils import day_review_service as service


def test_due_dates_wait_for_the_bundle_hour_and_never_include_today(monkeypatch):
    monkeypatch.setenv('OMI_DAY_BUNDLE_HOUR', '3')
    pending = ['2026-10-20', '2026-10-21', '2026-10-22']
    assert scheduler.due_dates('u1', datetime(2026, 10, 22, 2, 59), pending) == []
    assert scheduler.due_dates('u1', datetime(2026, 10, 22, 3, 0), pending) == ['2026-10-20', '2026-10-21']


def test_auto_follows_the_hold_unless_set(monkeypatch):
    monkeypatch.delenv('OMI_DAY_BUNDLE_AUTO', raising=False)
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.delenv('OMI_CONVERSATION_HOLD', raising=False)
    assert scheduler.auto_enabled() is True
    monkeypatch.setenv('OMI_DAY_BUNDLE_AUTO', 'off')
    assert scheduler.auto_enabled() is False
    monkeypatch.setenv('OMI_SELF_HOSTED', '0')
    monkeypatch.setenv('OMI_DAY_BUNDLE_AUTO', 'on')
    assert scheduler.auto_enabled() is True


class _Redis:
    def __init__(self):
        self.keys = {}

    def exists(self, key):
        return key in self.keys

    def set(self, key, value, ex=None):
        self.keys[key] = value


@pytest.fixture
def stack(monkeypatch):
    redis = _Redis()
    bundled = []
    monkeypatch.setenv('OMI_DAY_BUNDLE_AUTO', 'on')
    monkeypatch.setenv('OMI_DAY_BUNDLE_HOUR', '0')
    monkeypatch.setattr(scheduler.redis_db, 'r', redis)
    monkeypatch.setattr(scheduler.notification_db, 'get_user_time_zone', lambda uid: 'UTC')
    monkeypatch.setattr(
        service,
        'pending_dates',
        lambda uid, days=7: {'today': '2026-10-22', 'pending_dates': ['2026-10-21', '2026-10-22']},
    )

    def bundle(uid, date_str):
        if uid == 'broken':
            raise service.DayReviewError(502, 'model down')
        bundled.append((uid, date_str))
        return {}

    monkeypatch.setattr(service, 'prepare_draft', bundle)
    monkeypatch.setattr(scheduler.auto_approve, 'run_for_user', lambda uid, now=None: {})
    return redis, bundled


def test_run_once_bundles_due_days_for_every_active_user_and_backs_off_failures(stack):
    redis, bundled = stack
    users = lambda: ['broken', 'u1']  # noqa: E731
    now = datetime(2026, 10, 22, 4, 0)
    out = {uid: scheduler.bundle_user(uid, now=now) for uid in users()}
    assert out == {'broken': {'2026-10-21': 'error:502'}, 'u1': {'2026-10-21': 'drafted'}}
    assert bundled == [('u1', '2026-10-21')]
    # the failed day is not retried until the backoff key expires
    assert scheduler.bundle_user('broken', now=now) == {'2026-10-21': 'backoff'}
    assert scheduler.run_once(users=users, now=now) == {
        'broken': {'2026-10-21': 'backoff'},
        'u1': {'2026-10-21': 'drafted'},
    }


def test_run_once_is_a_no_op_when_auto_is_off(stack, monkeypatch):
    redis, bundled = stack
    monkeypatch.setenv('OMI_DAY_BUNDLE_AUTO', 'off')
    assert scheduler.run_once(users=lambda: ['u1']) == {} and bundled == []


def test_a_day_with_a_draft_waits_for_the_user_and_is_never_approved_by_the_job(stack, monkeypatch):
    """The overnight job only writes drafts: a day that has one is left for the user."""
    redis, bundled = stack
    monkeypatch.setattr(
        service,
        'pending_dates',
        lambda uid, days=7: {
            'today': '2026-10-22',
            'pending_dates': ['2026-10-20', '2026-10-21'],
            'draft_dates': ['2026-10-20'],
        },
    )
    approved = []
    monkeypatch.setattr(service, 'approve', lambda uid, d: approved.append(d))
    out = scheduler.run_once(users=lambda: ['u1'], now=datetime(2026, 10, 22, 4, 0))
    assert out == {'u1': {'2026-10-21': 'drafted'}} and bundled == [('u1', '2026-10-21')] and approved == []


def test_the_tick_runs_each_users_auto_approve(stack, monkeypatch):
    redis, bundled = stack
    monkeypatch.setattr(
        scheduler.auto_approve,
        'run_for_user',
        lambda uid, now=None: {'tasks': ['t1'], 'events': [], 'days': ['2026-10-21']} if uid == 'u2' else {},
    )
    out = scheduler.run_once(users=lambda: ['u2'], now=datetime(2026, 10, 22, 4, 0))
    assert out['u2']['auto_approved'] == 'tasks=1 events=0 days=1'


def test_bundle_user_looks_back_lookback_days_past_days_not_counting_today(monkeypatch):
    """A day held LOOKBACK_DAYS ago is still bundled: pending_dates counts today as its first day."""
    asked = {}

    def pending_dates(uid, days=7):
        asked['days'] = days
        return {'today': '2026-11-06', 'pending_dates': ['2026-10-30']}

    bundled = []
    monkeypatch.setattr(service, 'pending_dates', pending_dates)
    monkeypatch.setattr(service, 'prepare_draft', lambda uid, date_str: bundled.append(date_str))
    monkeypatch.setattr(scheduler.notification_db, 'get_user_time_zone', lambda uid: None)
    monkeypatch.setattr(scheduler.redis_db, 'r', _Redis())
    monkeypatch.setenv('OMI_DAY_BUNDLE_HOUR', '3')
    out = scheduler.bundle_user('u1', now=datetime(2026, 11, 6, 9, 0))
    assert asked['days'] == scheduler.LOOKBACK_DAYS + 1
    assert out == {'2026-10-30': 'drafted'} and bundled == ['2026-10-30']
