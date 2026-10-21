"""utils/local_auth.py: the local-login attempt limiter.

It resolves the Redis client at call time (database.redis_db.r), so a test, or
an isolated suite that stubs database.redis_db, controls it without patching
this module.
"""

import pytest
from fastapi import HTTPException

import database.redis_db as redis_db
import utils.local_auth as local_auth


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def expire(self, key, seconds):
        return True

    def set(self, key, value, ex=None):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_db, 'r', fake)
    return fake


def test_a_username_over_budget_is_locked_out_until_cleared(fake_redis):
    for attempt in range(local_auth._MAX_ATTEMPTS_PER_WINDOW):
        local_auth.enforce_login_rate_limit(client_ip=f'10.0.0.{attempt}', username='Owner')

    with pytest.raises(HTTPException) as exc:
        local_auth.enforce_login_rate_limit(client_ip='10.0.0.250', username='owner')
    assert exc.value.status_code == 429
    with pytest.raises(HTTPException):
        local_auth.enforce_login_rate_limit(client_ip='10.0.0.251', username='owner')

    local_auth._clear_rate_limit(client_ip='10.0.0.251', username='owner')
    local_auth.enforce_login_rate_limit(client_ip='10.0.0.252', username='owner')
