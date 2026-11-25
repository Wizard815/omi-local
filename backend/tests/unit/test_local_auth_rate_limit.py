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


def test_login_client_ip_ignores_forwarded_headers_from_untrusted_peers(monkeypatch):
    monkeypatch.delenv(local_auth.TRUSTED_PROXY_NETWORKS_ENV, raising=False)
    headers = {'cf-connecting-ip': '203.0.113.9', 'x-forwarded-for': '203.0.113.9'}

    assert local_auth.login_client_ip('172.18.0.5', headers) == '172.18.0.5'
    assert local_auth.login_client_ip(None, headers) == 'unknown'


def test_login_client_ip_uses_the_trusted_proxys_forwarded_address(monkeypatch):
    monkeypatch.setenv(local_auth.TRUSTED_PROXY_NETWORKS_ENV, '172.18.0.0/16, not-a-network')

    assert local_auth.login_client_ip('172.18.0.5', {'cf-connecting-ip': '203.0.113.9'}) == '203.0.113.9'
    # Only the right-most X-Forwarded-For entry is the proxy's own.
    assert local_auth.login_client_ip('172.18.0.5', {'x-forwarded-for': '6.6.6.6, 198.51.100.7'}) == '198.51.100.7'
    # Junk falls back to the peer, and other peers stay untrusted.
    assert local_auth.login_client_ip('172.18.0.5', {'cf-connecting-ip': 'nonsense'}) == '172.18.0.5'
    assert local_auth.login_client_ip('10.0.0.8', {'cf-connecting-ip': '203.0.113.9'}) == '10.0.0.8'


def test_tunnel_callers_no_longer_share_one_lockout(fake_redis, monkeypatch):
    monkeypatch.setenv(local_auth.TRUSTED_PROXY_NETWORKS_ENV, '172.18.0.2/32')
    attacker = local_auth.login_client_ip('172.18.0.2', {'cf-connecting-ip': '203.0.113.9'})
    owner = local_auth.login_client_ip('172.18.0.2', {'cf-connecting-ip': '198.51.100.7'})
    for attempt in range(local_auth._MAX_ATTEMPTS_PER_WINDOW):
        local_auth.enforce_login_rate_limit(client_ip=attacker, username=f'guess-{attempt}')
    with pytest.raises(HTTPException):
        local_auth.enforce_login_rate_limit(client_ip=attacker, username='guess-x')

    local_auth.enforce_login_rate_limit(client_ip=owner, username='owner')
