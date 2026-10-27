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

    monkeypatch.setattr(service, 'bundle', bundle)
    return redis, bundled


def test_run_once_bundles_due_days_for_every_active_user_and_backs_off_failures(stack):
    redis, bundled = stack
    users = lambda: ['broken', 'u1']  # noqa: E731
    now = datetime(2026, 10, 22, 4, 0)
    out = {uid: scheduler.bundle_user(uid, now=now) for uid in users()}
    assert out == {'broken': {'2026-10-21': 'error:502'}, 'u1': {'2026-10-21': 'bundled'}}
    assert bundled == [('u1', '2026-10-21')]
    # the failed day is not retried until the backoff key expires
    assert scheduler.bundle_user('broken', now=now) == {'2026-10-21': 'backoff'}
    assert scheduler.run_once(users=users, now=now) == {
        'broken': {'2026-10-21': 'backoff'},
        'u1': {'2026-10-21': 'bundled'},
    }


def test_run_once_is_a_no_op_when_auto_is_off(stack, monkeypatch):
    monkeypatch.setenv('OMI_DAY_BUNDLE_AUTO', 'off')
    assert scheduler.run_once(users=lambda: ['u1']) == {}
