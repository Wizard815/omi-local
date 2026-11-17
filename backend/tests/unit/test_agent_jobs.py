"""Send to agent (omi-local): Odysseus for simple asks, Hermes for agentic jobs (its approval
questions answered from Omi), Studio for coding agents (results on its signed webhook).

Fakes: tests/unit/fixtures/outbound_fakes.py, shaped after hermes-agent api_server_runs.py
(POST /v1/runs -> 202 {run_id}; GET /v1/runs/{id}; POST /v1/runs/{id}/approval), odysseus
routes/session_routes.py + routes/chat_routes.py, and hermes-studio controllers/orb.ts +
services/webhooks/dispatcher.ts."""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database.agent_jobs as jobs_db  # noqa: E402
import database.conversations as conversations_db  # noqa: E402
import database.integrations_out as settings_db  # noqa: E402
import database.notifications as notification_db  # noqa: E402
import database.outbound_work as work_db  # noqa: E402
from routers import agent_jobs as router_module  # noqa: E402
from tests.unit.fixtures.outbound_fakes import FakeFirestore, HermesFake, OdysseusFake, StudioFake  # noqa: E402
from utils import agent_jobs, outbound_http, outbound_worker  # noqa: E402
from utils.other import endpoints as auth  # noqa: E402

CONVERSATION = {
    'id': 'conv-1',
    'created_at': datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
    'started_at': datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
    'finished_at': datetime(2026, 11, 16, 9, 20, tzinfo=timezone.utc),
    'structured': {
        'title': 'Garage shelves',
        'overview': 'Planned shelves for the garage; need a cut list.',
        'action_items': [{'description': 'Buy 3 boards', 'completed': False}],
        'emoji': '🪚',
        'category': 'other',
    },
    'transcript_segments': [],
    'status': 'completed',
}


@pytest.fixture
def env(monkeypatch):
    db = FakeFirestore()
    for module in (settings_db, work_db, jobs_db):
        monkeypatch.setattr(module, 'get_firestore_client', lambda: db)
    rows = {'conv-1': dict(CONVERSATION)}
    monkeypatch.setattr(conversations_db, 'get_conversation', lambda uid, cid, **k: rows.get(cid))
    monkeypatch.setattr(conversations_db, 'update_conversation', lambda uid, cid, data: rows[cid].update(data))
    monkeypatch.setattr(notification_db, 'get_user_time_zone', lambda uid: 'UTC')
    monkeypatch.setattr(outbound_http, '_target', lambda url: (url, {'headers': {}, 'extensions': {}}))
    pushed = []
    monkeypatch.setattr(agent_jobs, 'dispatch_agent_job', lambda uid, title, body, job_id: pushed.append((title, body)))
    kicked = []

    def no_background(coro, name):
        kicked.append(name)
        coro.close()

    monkeypatch.setattr(agent_jobs, 'start_background_task', no_background)
    for name in (
        'HERMES_API_URL',
        'HERMES_API_KEY',
        'ODYSSEUS_URL',
        'ODYSSEUS_API_TOKEN',
        'STUDIO_URL',
        'STUDIO_WEBHOOK_SECRET',
    ):
        monkeypatch.delenv(name, raising=False)
    return db, rows, pushed, kicked


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[auth.get_current_user_uid] = lambda: 'u1'
    return TestClient(app)


def test_the_preview_is_the_prompt_and_omis_context_and_suggests_the_agent(client, env):
    out = client.post(
        '/v1/agent-jobs/preview',
        json={'target': 'odysseus', 'prompt': 'How long should the boards be?', 'conversation_ids': ['conv-1']},
    ).json()
    assert out['suggested_target'] == 'odysseus' and out['title'] == 'Garage shelves'
    assert out['content'].startswith('How long should the boards be?\n\n---\nContext from Omi:')
    assert 'Planned shelves for the garage' in out['content'] and '- Buy 3 boards' in out['content']
    long_ask = 'Research shelf brackets, compare three options with prices, and draft a shopping list. ' * 5
    assert (
        client.post('/v1/agent-jobs/preview', json={'target': 'hermes', 'prompt': long_ask}).json()['suggested_target']
        == 'hermes'
    )


def test_studio_text_is_cut_to_studios_limit_in_the_preview_itself(client, env):
    out = client.post(
        '/v1/agent-jobs/preview', json={'target': 'studio', 'prompt': 'x' * 19_900, 'conversation_ids': ['conv-1']}
    ).json()
    assert len(out['content']) <= 20_000 and out['content'].endswith('[context cut to fit]')


def test_sending_needs_the_connection_turned_on(client):
    out = client.post('/v1/agent-jobs', json={'target': 'hermes', 'prompt': 'Do it'})
    assert out.status_code == 409 and 'Connections' in out.json()['detail']


@pytest.mark.asyncio
async def test_a_hermes_job_relays_its_approval_and_stores_the_result(env):
    db, rows, pushed, kicked = env
    hermes = HermesFake(output='Cut list: 3 x 120 cm boards.')
    try:
        settings_db.set_target('u1', 'hermes', {'enabled': True, 'url': hermes.url}, secrets={'token': hermes.key})
        request = {
            'target': 'hermes',
            'prompt': 'Make a cut list',
            'conversation_ids': ['conv-1'],
            'attach_result': True,
        }
        preview = agent_jobs.build('u1', request)
        job = await agent_jobs.create('u1', request)
        assert job['status'] == 'running' and job['external_id'] == 'run_0001'
        created = hermes.server.requests[0]
        assert created.path == '/v1/runs' and created.headers['Idempotency-Key'] == f"omi-job-{job['id']}"
        body = created.json()
        assert body['input'] == preview['content'] and body['session_id'] == f"omi-job-{job['id']}"
        assert 'approved exactly this text' in body['instructions']

        now = datetime.now(timezone.utc)
        await outbound_worker.run_due(now)
        waiting = jobs_db.get('u1', job['id'])
        assert waiting['status'] == 'waiting_for_approval'
        assert waiting['approval'] == {
            'request_id': 'req-1',
            'command': 'rm -rf /tmp/scratch',
            'description': 'Clean scratch',
            'choices': ['once', 'deny'],
        }
        assert [j['id'] for j in agent_jobs.waiting('u1')] == [job['id']]
        assert pushed[-1][0] == 'Hermes asks before it goes on'
        await outbound_worker.run_due(now + timedelta(seconds=20))
        assert len([p for p in pushed if 'asks' in p[0]]) == 1  # one push per question

        answered = await agent_jobs.answer_approval('u1', job['id'], 'once')
        assert answered['status'] == 'running'
        approval = hermes.server.requests[-1]
        assert approval.path == '/v1/runs/run_0001/approval' and approval.json() == {
            'choice': 'once',
            'request_id': 'req-1',
        }

        await outbound_worker.run_due(now + timedelta(seconds=40))
        done = jobs_db.get('u1', job['id'])
        assert done['status'] == 'completed' and done['result'] == 'Cut list: 3 x 120 cm boards.'
        assert pushed[-1][0] == 'Hermes finished: Garage shelves'
        assert rows['conv-1']['apps_results'][-1]['app_id'] == 'agent-job-hermes'
        assert work_db.all_items() == []
    finally:
        hermes.close()


@pytest.mark.asyncio
async def test_a_denied_hermes_question_ends_the_job(env):
    hermes = HermesFake()
    try:
        settings_db.set_target('u1', 'hermes', {'enabled': True, 'url': hermes.url}, secrets={'token': hermes.key})
        job = await agent_jobs.create('u1', {'target': 'hermes', 'prompt': 'Clean up'})
        now = datetime.now(timezone.utc)
        await outbound_worker.run_due(now)
        await agent_jobs.answer_approval('u1', job['id'], 'deny')
        await outbound_worker.run_due(now + timedelta(seconds=20))
        assert jobs_db.get('u1', job['id'])['status'] == 'failed'
        with pytest.raises(agent_jobs.AgentJobError):
            await agent_jobs.answer_approval('u1', job['id'], 'once')
    finally:
        hermes.close()


@pytest.mark.asyncio
async def test_an_odysseus_ask_is_one_chat_turn(env):
    db, rows, pushed, kicked = env
    ody = OdysseusFake(reply='Use 120 cm boards.')
    try:
        settings_db.set_target(
            'u1',
            'odysseus',
            {'enabled': True, 'url': ody.url, 'endpoint_id': 'ep-local', 'model': 'llama'},
            secrets={'token': ody.token},
        )
        job = await agent_jobs.create(
            'u1', {'target': 'odysseus', 'prompt': 'How long?', 'conversation_ids': ['conv-1']}
        )
        assert job['status'] == 'queued' and kicked == ['agent_job_odysseus']
        await outbound_worker.run_due(datetime.now(timezone.utc))
        session = ody.server.requests[0]
        assert session.path == '/api/session' and session.form() == {
            'name': 'Omi: Garage shelves',
            'endpoint_id': 'ep-local',
            'model': 'llama',
        }
        chat = ody.server.requests[1]
        assert chat.path == '/api/chat' and chat.json() == {'message': job['prompt'], 'session': 'sess-1'}
        done = jobs_db.get('u1', job['id'])
        assert (
            done['status'] == 'completed' and done['result'] == 'Use 120 cm boards.' and done['external_id'] == 'sess-1'
        )
        assert pushed[-1] == ('Odysseus finished: Garage shelves', 'Use 120 cm boards.')
    finally:
        ody.close()


@pytest.mark.asyncio
async def test_odysseus_reuses_a_chat_and_explains_a_missing_endpoint(env):
    ody = OdysseusFake()
    try:
        settings_db.set_target('u1', 'odysseus', {'enabled': True, 'url': ody.url}, secrets={'token': ody.token})
        job = await agent_jobs.create('u1', {'target': 'odysseus', 'prompt': 'Hi'})
        await outbound_worker.run_due(datetime.now(timezone.utc))
        failed = jobs_db.get('u1', job['id'])
        assert failed['status'] == 'failed' and 'endpoint' in failed['error'] and ody.server.requests == []
        ody.sessions['kept-chat'] = {}
        settings_db.set_target('u1', 'odysseus', {'session_id': 'kept-chat'})
        job = await agent_jobs.create('u1', {'target': 'odysseus', 'prompt': 'Hi again'})
        await outbound_worker.run_due(datetime.now(timezone.utc))
        assert [r.path for r in ody.server.requests] == ['/api/chat']
        assert jobs_db.get('u1', job['id'])['status'] == 'completed'
    finally:
        ody.close()


def _signed(secret, body, ts=None):
    ts = str(int(ts if ts is not None else time.time()))
    sig = 'sha256=' + hmac.new(secret.encode(), f'{ts}.'.encode() + body, hashlib.sha256).hexdigest()
    return {
        'X-Hermes-Timestamp': ts,
        'X-Hermes-Signature-256': sig,
        'X-Hermes-Event': 'chat.run.completed',
        'Content-Type': 'application/json',
    }


@pytest.mark.asyncio
async def test_a_studio_job_gets_its_result_on_the_signed_webhook(client, env, monkeypatch):
    db, rows, pushed, kicked = env
    studio = StudioFake()
    try:
        monkeypatch.setenv('STUDIO_WEBHOOK_SECRET', 'studio-shared-secret')
        settings_db.set_target(
            'u1',
            'studio',
            {'enabled': True, 'url': studio.url, 'default_agent': 'claude-code', 'workspace': '/srv/shelves'},
            secrets={'token': studio.token},
        )
        job = await agent_jobs.create('u1', {'target': 'studio', 'prompt': 'Write a cut-list script'})
        started = studio.server.requests[0].json()
        assert started == {
            'agentId': 'claude-code',
            'title': 'Write a cut-list script',
            'prompt': 'Write a cut-list script',
            'workspace': '/srv/shelves',
        }
        assert job['external_id'] == 'studio-sess-1' and job['status'] == 'running'

        event = {
            'schema_version': 1,
            'id': 'evt-1',
            'type': 'chat.run.completed',
            'subject': {'session_id': 'studio-sess-1', 'run_id': 'r1'},
            'summary': {'status': 'completed'},
            'message': {'role': 'assistant', 'text': 'Wrote cutlist.py', 'truncated': False},
        }
        raw = json.dumps(event).encode()
        bad = client.post('/v1/integrations/studio/events', content=raw, headers=_signed('wrong', raw))
        assert bad.status_code == 401 and jobs_db.get('u1', job['id'])['status'] == 'running'
        stale = client.post(
            '/v1/integrations/studio/events',
            content=raw,
            headers=_signed('studio-shared-secret', raw, time.time() - 3600),
        )
        assert stale.status_code == 401
        ok = client.post('/v1/integrations/studio/events', content=raw, headers=_signed('studio-shared-secret', raw))
        assert ok.status_code == 200 and ok.json() == {'ok': True, 'job_id': job['id']}
        done = jobs_db.get('u1', job['id'])
        assert done['status'] == 'completed' and done['result'] == 'Wrote cutlist.py'
        assert work_db.all_items() == []
        unknown = client.post(
            '/v1/integrations/studio/events', content=raw, headers=_signed('studio-shared-secret', raw)
        )
        assert unknown.status_code == 401  # the chat is no longer followed: same answer as a bad signature
    finally:
        studio.close()


@pytest.mark.asyncio
async def test_studio_status_is_polled_and_a_blocked_chat_is_shown(env):
    studio = StudioFake()
    try:
        settings_db.set_target('u1', 'studio', {'enabled': True, 'url': studio.url}, secrets={'token': studio.token})
        job = await agent_jobs.create('u1', {'target': 'studio', 'prompt': 'Refactor'})
        studio.work[0]['status'] = 'blocked'
        await outbound_worker.run_due(datetime.now(timezone.utc))
        blocked = jobs_db.get('u1', job['id'])
        assert blocked['status'] == 'waiting_for_approval' and 'Studio' in blocked['approval']['description']
        with pytest.raises(agent_jobs.AgentJobError) as caught:
            await agent_jobs.answer_approval('u1', job['id'], 'once')
        assert 'Studio' in caught.value.message
        studio.work[0]['status'] = 'done'
        await outbound_worker.run_due(datetime.now(timezone.utc) + timedelta(seconds=20))
        assert jobs_db.get('u1', job['id'])['status'] == 'completed'
    finally:
        studio.close()


def test_list_and_get_jobs(client, env):
    jobs_db.create('u1', 'j1', {'target': 'hermes', 'title': 'A', 'status': 'completed'})
    listed = client.get('/v1/agent-jobs').json()['jobs']
    assert [j['id'] for j in listed] == ['j1']
    assert client.get('/v1/agent-jobs/j1').json()['status'] == 'completed'
    assert client.get('/v1/agent-jobs/nope').status_code == 404


def test_the_signature_check_matches_studios_dispatcher():
    body = b'{"a":1}'
    ts = '1700000000'
    # dispatcher.ts signChatWebhookPayload: sha256=HMAC(secret, `${timestamp}.${rawBody}`)
    expected = 'sha256=' + hmac.new(b's', b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
    assert agent_jobs.verify_studio_signature('s', ts, body, expected, now=1700000100)
    assert not agent_jobs.verify_studio_signature('s', ts, body, expected, now=1700001000)
    assert not agent_jobs.verify_studio_signature('', ts, body, expected, now=1700000100)


def test_the_approvals_banner_lists_the_agents_questions(env, monkeypatch):
    from models.approvals import WaitingForApprovalResponse
    from utils import auto_approve

    monkeypatch.setattr(auto_approve.task_approval, 'waiting', lambda uid: [])
    monkeypatch.setattr(auto_approve.family_calendar, 'pending', lambda uid: ([], None))
    monkeypatch.setattr(
        auto_approve.day_review_service, 'pending_dates', lambda uid, days=7: {'draft_dates': [], 'today': '2026-11-17'}
    )
    monkeypatch.setattr(auto_approve.approval_db, 'get_settings', lambda uid: {})
    jobs_db.create(
        'u1',
        'j-ask',
        {
            'target': 'hermes',
            'title': 'Clean up',
            'status': 'waiting_for_approval',
            'approval': {'request_id': 'r', 'command': 'rm x', 'description': '', 'choices': ['once', 'deny']},
        },
    )
    jobs_db.create('u1', 'j-done', {'target': 'hermes', 'title': 'Other', 'status': 'completed'})
    summary = WaitingForApprovalResponse.model_validate(auto_approve.waiting_summary('u1'))
    assert [j.id for j in summary.agent_jobs] == ['j-ask'] and summary.agent_jobs[0].approval.command == 'rm x'
