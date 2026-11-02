"""database/day_bundle_users.py: per-member expiry, and the legacy set migrates."""

import time

import pytest

import database.day_bundle_users as users_db


class _Redis:
    def __init__(self):
        self.hashes = {}
        self.sets = {}

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = str(value)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hexists(self, key, field):
        return field in self.hashes.get(key, {})

    def hdel(self, key, *fields):
        for field in fields:
            self.hashes.get(key, {}).pop(field, None)

    def expire(self, key, seconds):
        pass

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, key):
        self.sets.pop(key, None)
        self.hashes.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(users_db.redis_db, 'r', fake)
    return fake


def test_only_recently_active_members_are_listed_and_stale_ones_are_dropped(redis):
    users_db.mark_active('fresh')
    redis.hset(users_db.KEY, 'retired', int(time.time()) - users_db.INACTIVE_TTL_SECONDS - 60)
    assert users_db.active_users() == ['fresh']
    # the stale member was removed, not just filtered, so it never costs another read
    assert 'retired' not in redis.hgetall(users_db.KEY)


def test_the_legacy_set_migrates_once_and_keeps_newer_timestamps(redis):
    redis.sets[users_db.LEGACY_KEY] = {'old-a', b'old-b'}
    # old-a already re-registered with its own stamp: migration must not overwrite it
    stamped = str(int(time.time()) - 5)
    redis.hset(users_db.KEY, 'old-a', stamped)
    assert sorted(users_db.active_users()) == ['old-a', 'old-b']
    assert redis.hgetall(users_db.KEY)['old-a'] == stamped
    assert users_db.LEGACY_KEY not in redis.sets  # migrated exactly once


def test_redis_failures_are_swallowed(redis, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError('down')

    monkeypatch.setattr(redis, 'hset', boom)
    users_db.mark_active('u1')  # no raise
    monkeypatch.setattr(redis, 'hgetall', boom)
    assert users_db.active_users() == []
