"""Send to Coinpile (omi-local): on the user's explicit tap, a note (write_note) or a transaction
(record_transaction) goes to Coinpile's MCP, which only asks: nothing changes there until Yes on
its Agent page (coinpile/src/coinpile/agent.py mcp_handle). Fake: outbound_fakes.McpFake."""

import os

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database.action_items as action_items_db  # noqa: E402
import database.conversations as conversations_db  # noqa: E402
import database.integrations_out as settings_db  # noqa: E402
import database.outbound_receipts as receipts_db  # noqa: E402
from routers import integrations_outbound as router_module  # noqa: E402
from tests.unit.fixtures.outbound_fakes import FakeFirestore, McpFake  # noqa: E402
from utils import outbound_http  # noqa: E402
from utils.other import endpoints as auth  # noqa: E402


@pytest.fixture
def setup(monkeypatch):
    db = FakeFirestore()
    for module in (settings_db, receipts_db):
        monkeypatch.setattr(module, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(outbound_http, '_target', lambda url: (url, {'headers': {}, 'extensions': {}}))
    monkeypatch.setattr(
        conversations_db,
        'get_conversation',
        lambda uid, cid, **k: (
            {'id': cid, 'structured': {'title': 'Plumber', 'overview': 'Paid the plumber $120 cash.'}}
            if cid == 'c1'
            else None
        ),
    )
    monkeypatch.setattr(
        action_items_db,
        'get_action_item',
        lambda uid, tid: {'description': 'Pay the plumber $120'} if tid == 't1' else None,
    )
    monkeypatch.delenv('COINPILE_URL', raising=False)
    monkeypatch.setattr(router_module, 'get_user_webhook_db', lambda uid, wtype: '')
    coinpile = McpFake('cp_wizard_secret', '/api/v1/mcp')
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[auth.get_current_user_uid] = lambda: 'u1'
    yield TestClient(app), coinpile, db
    coinpile.close()


def _on(coinpile):
    settings_db.set_target(
        'u1', 'coinpile', {'enabled': True, 'url': coinpile.url}, secrets={'token': 'cp_wizard_secret'}
    )


def test_nothing_is_sent_while_coinpile_is_off(setup):
    client, coinpile, db = setup
    out = client.post('/v1/integrations/coinpile/send', json={'kind': 'note', 'title': 'x', 'body': 'y'}).json()
    assert out['ok'] is False and 'Connections' in out['error'] and coinpile.calls == []


def test_a_note_is_drafted_from_the_conversation_and_sent_as_write_note(setup):
    client, coinpile, db = setup
    _on(coinpile)
    draft = client.get('/v1/integrations/coinpile/draft', params={'conversation_id': 'c1'}).json()
    assert draft['title'] == 'Plumber' and draft['body'] == 'Paid the plumber $120 cash.' and draft['kind'] == 'note'
    out = client.post(
        '/v1/integrations/coinpile/send', json={**draft, 'body': 'Paid the plumber $120 cash (edited).'}
    ).json()
    assert out['ok'] is True and 'Nothing has changed yet' in out['answer']
    assert coinpile.calls == [
        {'name': 'write_note', 'arguments': {'title': 'Plumber', 'body': 'Paid the plumber $120 cash (edited).'}}
    ]
    assert receipts_db.receipts('u1', 'coinpile_c1')[0]['target'] == 'coinpile'


def test_a_transaction_needs_an_account_and_goes_as_record_transaction(setup):
    client, coinpile, db = setup
    _on(coinpile)
    accounts = client.get('/v1/integrations/coinpile/accounts').json()
    assert accounts == {'accounts': [{'id': 'ff:1', 'name': 'Cash', 'type': 'cash'}], 'error': ''}
    missing = client.post('/v1/integrations/coinpile/send', json={'kind': 'transaction', 'amount': -120}).json()
    assert missing['ok'] is False and 'account' in missing['error']
    out = client.post(
        '/v1/integrations/coinpile/send',
        json={
            'kind': 'transaction',
            'task_id': 't1',
            'account_id': 'ff:1',
            'amount': -120,
            'description': 'Plumber',
            'date': '2026-11-16',
        },
    ).json()
    assert out['ok'] is True
    assert coinpile.calls[-1] == {
        'name': 'record_transaction',
        'arguments': {'account_id': 'ff:1', 'amount': -120.0, 'description': 'Plumber', 'date': '2026-11-16'},
    }


def test_a_task_drafts_a_note_too(setup):
    client, coinpile, db = setup
    _on(coinpile)
    draft = client.get('/v1/integrations/coinpile/draft', params={'task_id': 't1'}).json()
    assert draft['title'] == 'Pay the plumber $120' and draft['task_id'] == 't1'
    assert client.get('/v1/integrations/coinpile/draft', params={'task_id': 'nope'}).status_code == 404
