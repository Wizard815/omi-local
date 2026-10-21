"""Self-hosted hourly job runner (utils/other/local_scheduler.py) and the
timezone endpoint the app calls instead of FCM registration.

Before these, an omi-local deployment never ran the daily-summary job (no
Cloud Scheduler) and never learned the user's time zone (FCM registration —
the only writer — is disabled), so recaps only existed on demand and used UTC
day boundaries.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import routers.notifications as notifications_router
from models.other import SyncUserTimeZoneRequest
from utils.other import local_scheduler


def test_next_tick_is_the_upcoming_minute_two():
    at = datetime(2026, 10, 11, 21, 59, 30, tzinfo=timezone.utc)
    assert local_scheduler.seconds_until_next_tick(at) == 2 * 60 + 30
    on_tick = datetime(2026, 10, 11, 22, 2, 0, tzinfo=timezone.utc)
    assert local_scheduler.seconds_until_next_tick(on_tick) == 3600  # never fires twice in one hour


def test_a_failing_tick_does_not_stop_the_loop():
    calls = []
    slept = []

    async def job():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError('LLM down for this hour')

    async def fake_sleep(seconds):
        slept.append(seconds)

    asyncio.run(
        local_scheduler.run_hourly(
            job,
            now=lambda: datetime(2026, 10, 11, 21, 0, 0, tzinfo=timezone.utc),
            sleep=fake_sleep,
            max_ticks=3,
        )
    )
    assert calls == [0, 1, 2]
    assert slept == [120.0, 120.0, 120.0]


@pytest.mark.parametrize('value,enabled', [('1', True), ('true', True), ('0', False), ('', False)])
def test_scheduler_is_opt_in(monkeypatch, value, enabled):
    monkeypatch.setenv(local_scheduler.SCHEDULER_ENV, value)
    assert local_scheduler.local_scheduler_enabled() is enabled


def test_time_zone_endpoint_validates_and_writes(monkeypatch):
    """The app's PUT /v1/users/time-zone (upstream's handler) records the zone without an FCM token."""
    synced = []
    monkeypatch.setattr(
        notifications_router.notification_db,
        'sync_user_time_zone_from_client',
        lambda uid, tz: synced.append((uid, tz)) or tz,
    )

    notifications_router.sync_user_time_zone(SyncUserTimeZoneRequest(time_zone='America/Chicago'), uid='uid-1')
    assert synced == [('uid-1', 'America/Chicago')]

    with pytest.raises(ValidationError):
        SyncUserTimeZoneRequest(time_zone='Mars/Olympus')
