"""Connections settings (omi-local): GET/PUT /v1/integrations/outbound, Test connection, the
address rule for LAN targets, and the developer-webhook double-send guard."""

import json
import os

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database.integrations_out as store  # noqa: E402
from routers import integrations_outbound as router_module  # noqa: E402
from tests.unit.fixtures.outbound_fakes import FakeFirestore, HermesFake, WikiFake  # noqa: E402
from utils import integrations_out, outbound_http  # noqa: E402
from utils.other import endpoints as auth  # noqa: E402

ENV_NAMES = (
    'LLMWIKI_URL',
    'HERMES_API_URL',
    'HERMES_API_KEY',
    'ODYSSEUS_URL',
    'ODYSSEUS_API_TOKEN',
    'STUDIO_URL',
    'STUDIO_WEBHOOK_SECRET',
    'COINPILE_URL',
    'ORB_URL',
)


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeFirestore()
    monkeypatch.setattr(store, 'get_firestore_client', lambda: db)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(router_module, 'get_user_webhook_db', lambda uid, wtype: '')
    return db


@pytest.fixture
def loopback_ok(monkeypatch):
    """The fakes listen on 127.0.0.1, which the real address rule refuses: let them through."""
    monkeypatch.setattr(outbound_http, '_target', lambda url: (url, {'headers': {}, 'extensions': {}}))


@pytest.fixture
def client(fake_db):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[auth.get_current_user_uid] = lambda: 'u1'
    return TestClient(app)


def test_every_target_starts_off_with_no_token(client):
    body = client.get('/v1/integrations/outbound').json()
    for name in integrations_out.TARGETS:
        assert body[name]['enabled'] is False and body[name]['has_token'] is False
    assert body['wiki']['require_manual_approval'] is True
    assert body['wiki']['send_kept_conversations'] == 'off'
    assert body['conversation_webhook_to_wiki'] is False


def test_tokens_are_write_only_and_stored_encrypted(client, fake_db):
    out = client.put(
        '/v1/integrations/outbound',
        json={'wiki': {'enabled': True, 'url': 'http://192.168.1.5:27123/', 'token': 'fam-secret-token'}},
    )
    assert out.status_code == 200
    body = out.json()
    assert body['wiki']['has_token'] is True and body['wiki']['url'] == 'http://192.168.1.5:27123'
    assert 'fam-secret-token' not in json.dumps(body)
    stored = fake_db.dump(('users', 'u1'))['users/u1/integrations/outbound']['wiki']
    assert stored['token_enc'] and stored['token_enc'] != 'fam-secret-token'
    assert integrations_out.effective('u1')['wiki']['token'] == 'fam-secret-token'
    # Omitted keeps it; '' clears it.
    client.put('/v1/integrations/outbound', json={'wiki': {'send_day_reviews': False}})
    assert integrations_out.effective('u1')['wiki']['token'] == 'fam-secret-token'
    cleared = client.put('/v1/integrations/outbound', json={'wiki': {'token': ''}}).json()
    assert cleared['wiki']['has_token'] is False


def test_a_bad_address_is_refused(client):
    out = client.put('/v1/integrations/outbound', json={'odysseus': {'url': 'ftp://nas.local'}})
    assert out.status_code == 422 and 'odysseus' in out.json()['detail']


def test_env_defaults_apply_until_the_user_sets_their_own(client, monkeypatch):
    monkeypatch.setenv('HERMES_API_URL', 'http://192.168.1.9:8642')
    monkeypatch.setenv('HERMES_API_KEY', 'household-hermes-key')
    monkeypatch.setenv('LLMWIKI_URL', 'http://192.168.1.5:27123')
    body = client.get('/v1/integrations/outbound').json()
    assert body['hermes']['url'] == 'http://192.168.1.9:8642' and body['hermes']['url_from_env'] is True
    assert body['hermes']['has_token'] is True and body['hermes']['token_from_env'] is True
    assert body['hermes']['enabled'] is False  # an env address alone never sends anything
    assert body['wiki']['url_from_env'] is True and body['wiki']['has_token'] is False
    own = client.put('/v1/integrations/outbound', json={'hermes': {'url': 'http://10.0.0.4:8642'}}).json()
    assert own['hermes']['url'] == 'http://10.0.0.4:8642' and own['hermes']['url_from_env'] is False


def test_test_connection_probes_the_wiki_with_the_token(client, loopback_ok):
    wiki = WikiFake()
    try:
        client.put('/v1/integrations/outbound', json={'wiki': {'url': wiki.url, 'token': 't-wizard'}})
        ok = client.post('/v1/integrations/outbound/wiki/test').json()
        assert ok['ok'] is True and 'Wizard' in ok['detail']
        rpc = wiki.server.requests[-1].json()
        assert rpc['method'] == 'tools/call' and rpc['params']['name'] == 'whoami'
        client.put('/v1/integrations/outbound', json={'wiki': {'token': 'wrong'}})
        bad = client.post('/v1/integrations/outbound/wiki/test').json()
        assert bad['ok'] is False and '401' in bad['error']
        assert client.get('/v1/integrations/outbound').json()['wiki']['last_check_ok'] is False
    finally:
        wiki.close()


def test_test_connection_reaches_hermes_models(client, loopback_ok):
    hermes = HermesFake()
    try:
        client.put('/v1/integrations/outbound', json={'hermes': {'url': hermes.url, 'token': hermes.key}})
        assert client.post('/v1/integrations/outbound/hermes/test').json()['ok'] is True
        assert hermes.server.requests[-1].path == '/v1/models'
    finally:
        hermes.close()


def test_without_an_address_the_test_says_so(client):
    out = client.post('/v1/integrations/outbound/coinpile/test').json()
    assert out == {'ok': False, 'error': 'No address set', 'detail': ''}


def _literal_dns(monkeypatch):
    """Resolve IP literals without the network (the hermetic test guard blocks real lookups)."""
    import socket

    from utils import http_client

    monkeypatch.setattr(
        http_client.socket,
        'getaddrinfo',
        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (host, 0))],
    )


def test_lan_targets_follow_the_webhook_address_rule(monkeypatch):
    """Loopback is never allowed; a private address only when the operator listed it."""
    _literal_dns(monkeypatch)
    monkeypatch.delenv('OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS', raising=False)
    with pytest.raises(Exception):
        outbound_http._target('http://127.0.0.1:27123/family-mcp')
    with pytest.raises(Exception):
        outbound_http._target('http://192.168.1.5:27123/family-mcp')
    monkeypatch.setenv('OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS', '192.168.1.0/24')
    pinned, extra = outbound_http._target('http://192.168.1.5:27123/family-mcp')
    assert pinned == 'http://192.168.1.5:27123/family-mcp' and extra['headers']['Host'] == '192.168.1.5:27123'
    with pytest.raises(Exception):
        outbound_http._target('http://127.0.0.1:27123/family-mcp')


@pytest.mark.asyncio
async def test_a_refused_address_is_reported_not_sent(monkeypatch):
    _literal_dns(monkeypatch)
    monkeypatch.delenv('OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS', raising=False)
    with pytest.raises(outbound_http.OutboundError) as caught:
        await outbound_http.request('POST', 'http://192.168.77.7:9/x', json_body={})
    assert caught.value.kind == 'blocked' and 'OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS' in caught.value.message


def test_dev_webhook_to_the_same_wiki_is_skipped_while_send_to_wiki_is_on(client, monkeypatch):
    client.put(
        '/v1/integrations/outbound',
        json={'wiki': {'enabled': True, 'url': 'http://192.168.1.5:27123', 'token': 't'}},
    )
    hook = 'http://192.168.1.5:27123/family/conversation?key=abc'
    assert integrations_out.wiki_owns_conversation_webhook('u1', hook) is True
    assert integrations_out.wiki_owns_conversation_webhook('u1', 'http://192.168.1.6:27123/x') is False
    monkeypatch.setattr(router_module, 'get_user_webhook_db', lambda uid, wtype: hook)
    assert client.get('/v1/integrations/outbound').json()['conversation_webhook_to_wiki'] is True
    client.put('/v1/integrations/outbound', json={'wiki': {'enabled': False}})
    assert integrations_out.wiki_owns_conversation_webhook('u1', hook) is False


@pytest.mark.asyncio
async def test_conversation_created_webhook_skips_the_wiki_it_now_sends_to(monkeypatch):
    from utils import webhooks

    posted = []
    monkeypatch.setattr(webhooks, 'user_webhook_status_db', lambda uid, wtype: True)
    monkeypatch.setattr(
        webhooks, 'get_user_webhook_db', lambda uid, wtype: 'http://192.168.1.5:27123/family/conversation'
    )
    monkeypatch.setattr(integrations_out, 'wiki_owns_conversation_webhook', lambda uid, url: True)

    async def never(*args, **kwargs):
        posted.append(args)

    monkeypatch.setattr(webhooks, '_post_dev_webhook', never)

    class Conv:
        is_locked = False

    await webhooks.conversation_created_webhook('u1', Conv())
    assert posted == []
