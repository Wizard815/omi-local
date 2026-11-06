"""CalDAV auto-sync: a task or day-summary change marks the member dirty and the short pass syncs
them within seconds; the full pass runs every CALDAV_SYNC_SECONDS."""

import asyncio
import os

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402

import database.action_items as action_items_db  # noqa: E402
from utils.caldav import sync  # noqa: E402
from utils.other import local_scheduler  # noqa: E402


class _Redis:
    def __init__(self):
        self.sets = {}

    def sadd(self, key, member):
        return (
            int(member not in self.sets.setdefault(key, set()))
            if not self.sets.setdefault(key, set()).add(member)
            else 1
        )

    def spop(self, key, count=1):
        members = self.sets.get(key, set())
        out = [members.pop() for _ in range(min(count, len(members)))]
        return [m.encode() for m in out]


@pytest.fixture
def redis(monkeypatch):
    r = _Redis()
    monkeypatch.setattr(sync.redis_db, 'r', r)
    return r


def test_mark_dirty_then_run_dirty_syncs_each_user_once(redis, monkeypatch):
    synced = []
    monkeypatch.setattr(sync, 'run_for_user', lambda uid: synced.append(uid) or {'tasks_pushed': 1})
    sync.mark_dirty('u1')
    sync.mark_dirty('u1')
    sync.mark_dirty('u2')
    out = sync.run_dirty()
    assert sorted(synced) == ['u1', 'u2'] and set(out) == {'u1', 'u2'}  # a set: no order to assert
    assert sync.run_dirty() == {} and len(synced) == 2, 'nothing left after the pass'


def test_a_user_without_a_server_is_skipped_quietly(redis, monkeypatch):
    monkeypatch.setattr(sync, 'run_for_user', lambda uid: None)
    sync.mark_dirty('u1')
    assert sync.run_dirty() == {}


def test_redis_trouble_never_breaks_a_write(monkeypatch):
    class _Broken:
        def sadd(self, *a):
            raise RuntimeError('down')

        def spop(self, *a):
            raise RuntimeError('down')

    monkeypatch.setattr(sync.redis_db, 'r', _Broken())
    sync.mark_dirty('u1')
    assert sync.take_dirty() == []


def test_action_item_writes_mark_the_user_dirty(redis, monkeypatch):
    inner = action_items_db.create_action_item.__wrapped__
    monkeypatch.setattr(action_items_db, 'create_action_item', action_items_db._marking(lambda uid, data, **k: 'id-1'))
    assert inner is not None
    assert action_items_db.create_action_item('u9', {'description': 'x'}) == 'id-1'
    assert redis.sets.get(sync.DIRTY_KEY) == {'u9'}


def test_caldav_sync_seconds_env(monkeypatch):
    monkeypatch.delenv('CALDAV_SYNC_SECONDS', raising=False)
    assert local_scheduler.caldav_sync_seconds() == 900.0
    monkeypatch.setenv('CALDAV_SYNC_SECONDS', '120')
    assert local_scheduler.caldav_sync_seconds() == 120.0
    monkeypatch.setenv('CALDAV_SYNC_SECONDS', '5')
    assert local_scheduler.caldav_sync_seconds() == 60.0, 'never below a minute'
    monkeypatch.setenv('CALDAV_SYNC_SECONDS', 'soon')
    assert local_scheduler.caldav_sync_seconds() == 900.0


def test_run_every_keeps_going_past_a_failure():
    calls = []
    slept = []

    async def job():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('first run fails')

    async def sleep(seconds):
        slept.append(seconds)

    asyncio.run(local_scheduler.run_every(20, job, sleep=sleep, max_ticks=3))
    assert len(calls) == 3 and slept == [20, 20, 20]
