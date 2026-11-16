"""OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS: a self-hosted operator may let webhooks reach
named LAN addresses (e.g. a home LLMWIKI vault), and nothing else private.

DNS is mocked (utils.http_client.socket.getaddrinfo); no network calls.
"""

import socket

import pytest

from utils.http_client import (
    UnsafeWebhookURLError,
    assert_public_http_url,
    assert_public_http_url_all,
    safe_request_target,
)

ENV = 'OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS'


def _resolve(monkeypatch, *ips):
    rows = [
        (
            (socket.AF_INET6, socket.SOCK_STREAM, 6, '', (ip, 0, 0, 0))
            if ':' in ip
            else (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 0))
        )
        for ip in ips
    ]
    monkeypatch.setattr('utils.http_client.socket.getaddrinfo', lambda host, port: rows)


def test_private_address_blocked_by_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    _resolve(monkeypatch, '192.168.20.5')
    with pytest.raises(UnsafeWebhookURLError):
        assert_public_http_url('http://llmwiki.lan:27123/family/conversation')


def test_allowed_host_passes_and_is_pinned(monkeypatch):
    monkeypatch.setenv(ENV, '192.168.20.5/32')
    _resolve(monkeypatch, '192.168.20.5')
    assert assert_public_http_url('http://llmwiki.lan:27123/family/conversation') == '192.168.20.5'
    pinned, extra = safe_request_target('http://llmwiki.lan:27123/family/conversation?key=k')
    assert pinned == 'http://192.168.20.5:27123/family/conversation?key=k'
    assert extra['headers']['Host'] == 'llmwiki.lan:27123'


def test_only_the_listed_network(monkeypatch):
    monkeypatch.setenv(ENV, '192.168.20.0/24')
    _resolve(monkeypatch, '192.168.30.7')
    with pytest.raises(UnsafeWebhookURLError):
        assert_public_http_url('http://other.lan/')
    _resolve(monkeypatch, '192.168.20.9', '10.0.0.2')
    with pytest.raises(UnsafeWebhookURLError):
        assert_public_http_url_all('http://split.lan/')


@pytest.mark.parametrize(
    'entry,ip',
    [
        ('127.0.0.0/8', '127.0.0.1'),
        ('169.254.0.0/16', '169.254.169.254'),
        ('0.0.0.0/0', '192.168.1.1'),
        ('::1/128', '::1'),
        ('fe80::/10', 'fe80::1'),
        ('224.0.0.0/4', '224.0.0.1'),
    ],
)
def test_loopback_metadata_and_wide_entries_are_ignored(monkeypatch, entry, ip):
    monkeypatch.setenv(ENV, entry)
    _resolve(monkeypatch, ip)
    with pytest.raises(UnsafeWebhookURLError):
        assert_public_http_url('http://x.lan/')


def test_garbage_entries_are_ignored_and_public_still_works(monkeypatch):
    monkeypatch.setenv(ENV, 'not-a-network, ,192.168.20.5')
    _resolve(monkeypatch, '8.8.8.8')
    assert assert_public_http_url('https://example.com/') == '8.8.8.8'
    _resolve(monkeypatch, '192.168.20.5')
    assert assert_public_http_url('http://wiki.lan/') == '192.168.20.5'


def test_ipv4_mapped_ipv6_follows_the_ipv4_rule(monkeypatch):
    monkeypatch.setenv(ENV, '192.168.20.5/32')
    _resolve(monkeypatch, '::ffff:192.168.20.5')
    assert assert_public_http_url('http://wiki.lan/') == '::ffff:192.168.20.5'
    _resolve(monkeypatch, '::ffff:127.0.0.1')
    with pytest.raises(UnsafeWebhookURLError):
        assert_public_http_url('http://wiki.lan/')
