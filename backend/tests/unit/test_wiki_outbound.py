"""Send to wiki (omi-local): an approved day is queued and sent after the Undo window only while
that approval stands; auto-approved days only where the user allowed it; a second copy is a
"(revised n)" source; one conversation is sent on the user's tap with its edited title,
overview and action items; kept clips go out with "send when I keep a clip".

The wiki is a fake of the LLMWIKI bridge's /family-mcp (tests/unit/fixtures/outbound_fakes.py,
shapes from main.js handleFamilyRpc / memoryAddSource)."""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database.conversations as conversations_db  # noqa: E402
import database.day_reviews as day_reviews_db  # noqa: E402
import database.integrations_out as settings_db  # noqa: E402
import database.notifications as notification_db  # noqa: E402
import database.outbound_receipts as receipts_db  # noqa: E402
import database.outbound_work as work_db  # noqa: E402
from routers import integrations_outbound as router_module  # noqa: E402
from tests.unit.fixtures.outbound_fakes import FakeFirestore, McpFake, WikiFake  # noqa: E402
from utils import (  # noqa: E402
    auto_approve,
    day_review_service,
    mcp_targets,
    outbound_dispatch,
    outbound_http,
    outbound_worker,
    wiki_outbound,
)
from utils.other import endpoints as auth  # noqa: E402

DATE = '2026-11-16'
APPROVED = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 17, 8, 0, 5, tzinfo=timezone.utc)

REVIEW = {
    'status': 'finalized',
    'title': 'A Monday of quilts',
    'summary_text': 'You cut fabric. You called the plumber.',
    'sections': [
        {
            'id': 's1',
            'title': 'Quilting',
            'text': 'You cut fabric for the quilt.',
            'tags': ['quilt'],
            'conversation_ids': ['c-roll-1'],
        },
        {'id': 's2', 'title': 'House', 'text': 'You called the plumber.', 'tags': [], 'conversation_ids': ['c-roll-2']},
    ],
    'corrections': [],
    'kept_conversation_ids': ['c-kept-1'],
    'rolled_up_conversation_ids': ['c-roll-1', 'c-roll-2'],
    'approved_at': APPROVED,
    'time_zone': 'UTC',
}

CONVERSATIONS = {
    'c-kept-1': {
        'id': 'c-kept-1',
        'created_at': datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
        'started_at': datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
        'finished_at': datetime(2026, 11, 16, 9, 20, tzinfo=timezone.utc),
        'structured': {
            'title': 'Plumber visit (edited)',
            'overview': 'The plumber fixes the sink on Thursday for $120 cash.',
            'action_items': [{'description': 'Get $120 cash', 'completed': False}],
            'emoji': '🔧',
            'category': 'other',
        },
        'transcript_segments': [
            {'text': 'Thursday works, 120 cash.', 'speaker': 'SPEAKER_00', 'is_user': False, 'start': 0, 'end': 2}
        ],
        'status': 'completed',
        'starred': True,
        'discarded': False,
    }
}


@pytest.fixture
def env(monkeypatch):
    db = FakeFirestore()
    for module in (settings_db, work_db, receipts_db):
        monkeypatch.setattr(module, 'get_firestore_client', lambda: db)
    monkeypatch.setattr(day_reviews_db, 'db', db)
    monkeypatch.setattr(notification_db, 'get_user_time_zone', lambda uid: 'UTC')
    monkeypatch.setattr(conversations_db, 'get_conversation', lambda uid, cid, **k: CONVERSATIONS.get(cid))
    monkeypatch.setattr(outbound_http, '_target', lambda url: (url, {'headers': {}, 'extensions': {}}))
    monkeypatch.setenv('OMI_OUTBOUND_DAY_DELAY_SECONDS', '600')
    for name in ('LLMWIKI_URL', 'ORB_URL'):
        monkeypatch.delenv(name, raising=False)

    def fake_get_day(uid, date_str, include_drafts=True):
        rows = [
            {
                'id': 'c-kept-1',
                'title': 'Plumber visit (edited)',
                'overview': 'Sink on Thursday.',
                'started_at': datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
            },
            {
                'id': 'c-roll-1',
                'title': 'Fabric',
                'overview': '',
                'started_at': datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc),
            },
            {
                'id': 'c-roll-2',
                'title': 'Call',
                'overview': '',
                'started_at': datetime(2026, 11, 16, 11, 0, tzinfo=timezone.utc),
            },
        ]
        return {'date': date_str, 'conversations': rows}

    monkeypatch.setattr(day_review_service, 'get_day', fake_get_day)
    wiki = WikiFake()
    yield db, wiki
    wiki.close()


def _turn_on(wiki, **wiki_choices):
    settings_db.set_target(
        'u1', 'wiki', {'enabled': True, 'url': wiki.url, **wiki_choices}, secrets={'token': 't-wizard'}
    )


def _approve(review=None):
    review = dict(review or REVIEW)
    day_reviews_db.set_day_review('u1', DATE, review)
    return review


@pytest.mark.asyncio
async def test_an_approved_day_is_sent_once_after_the_undo_window(env):
    db, wiki = env
    _turn_on(wiki)
    review = _approve()
    keys = outbound_dispatch.day_approved('u1', DATE, review, 'user', now=APPROVED)
    assert keys == [f'wiki_day:u1:{DATE}']
    shown = day_reviews_db.get_day_review('u1', DATE)['outbound_receipts']
    assert shown[0]['target'] == 'wiki' and shown[0]['scheduled_at'] == APPROVED + timedelta(minutes=10)

    assert await outbound_worker.run_due(NOW) == {}  # still inside the Undo window
    assert wiki.files == {}

    out = await outbound_worker.run_due(APPROVED + timedelta(minutes=11))
    assert out == {f'wiki_day:u1:{DATE}': 'sent'}
    path = f'people/wizard/raw/sources/{DATE} - Omi day review.md'
    assert list(wiki.files) == [path]
    content = wiki.files[path]
    assert '# A Monday of quilts' in content and '## Quilting' in content and 'Tags: #quilt' in content
    assert 'omi://conversation/c-roll-1' in content
    assert '## Kept conversations' in content and 'Plumber visit (edited)' in content
    rpc = wiki.server.requests[-1].json()
    assert rpc['params']['name'] == 'memory_add_source' and rpc['params']['arguments']['date'] == DATE
    shown = day_reviews_db.get_day_review('u1', DATE)['outbound_receipts']
    assert shown[0]['sent_at'] and shown[0]['title'] == 'Omi day review' and shown[0]['revision'] == 0
    # Done: nothing left to send, a second tick sends nothing.
    assert work_db.all_items() == [] and await outbound_worker.run_due(APPROVED + timedelta(hours=1)) == {}
    assert len(wiki.files) == 1


@pytest.mark.asyncio
async def test_an_undone_approval_sends_nothing(env):
    db, wiki = env
    _turn_on(wiki)
    review = _approve()
    outbound_dispatch.day_approved('u1', DATE, review, 'user', now=APPROVED)
    # Approve all's Undo puts the draft back before the window ends.
    day_reviews_db.set_day_review('u1', DATE, {**REVIEW, 'status': 'draft', 'approved_at': None})
    out = await outbound_worker.run_due(APPROVED + timedelta(minutes=11))
    assert out == {f'wiki_day:u1:{DATE}': 'dropped'} and wiki.files == {} and wiki.server.requests == []


def test_a_draft_is_never_rendered_for_the_wiki(env):
    _approve({**REVIEW, 'status': 'draft'})
    with pytest.raises(wiki_outbound.WikiNotReady):
        wiki_outbound.day_markdown('u1', DATE)


@pytest.mark.asyncio
async def test_auto_approved_days_wait_for_the_users_permission(env):
    db, wiki = env
    _turn_on(wiki)
    review = _approve()
    assert outbound_dispatch.day_approved('u1', DATE, review, 'auto', now=APPROVED) == []
    receipt = day_reviews_db.get_day_review('u1', DATE)['outbound_receipts'][0]
    assert receipt['error'] == outbound_dispatch.AUTO_NOT_SENT and not receipt['sent_at']
    _turn_on(wiki, require_manual_approval=False)
    assert outbound_dispatch.day_approved('u1', DATE, review, 'auto', now=APPROVED) == [f'wiki_day:u1:{DATE}']


@pytest.mark.asyncio
async def test_a_reapproved_day_is_filed_as_a_revision(env):
    db, wiki = env
    _turn_on(wiki)
    review = _approve()
    outbound_dispatch.day_approved('u1', DATE, review, 'user', now=APPROVED)
    await outbound_worker.run_due(APPROVED + timedelta(minutes=11))
    later = APPROVED + timedelta(days=1)
    review = _approve({**REVIEW, 'title': 'A Monday of quilts, corrected', 'approved_at': later})
    outbound_dispatch.day_approved('u1', DATE, review, 'user', now=later)
    await outbound_worker.run_due(later + timedelta(minutes=11))
    assert sorted(wiki.files) == [
        f'people/wizard/raw/sources/{DATE} - Omi day review (revised 1).md',
        f'people/wizard/raw/sources/{DATE} - Omi day review.md',
    ]
    shown = day_reviews_db.get_day_review('u1', DATE)['outbound_receipts']
    assert [r['revision'] for r in shown] == [0, 1]


@pytest.mark.asyncio
async def test_a_copy_the_receipts_missed_moves_on_to_the_next_revision(env):
    db, wiki = env
    _turn_on(wiki)
    _approve()
    wiki.files[f'people/wizard/raw/sources/{DATE} - Omi day review.md'] = 'filed by hand'
    receipt = await wiki_outbound.send_day('u1', DATE)
    assert receipt['title'] == 'Omi day review (revised 1)' and not receipt['error']


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[auth.get_current_user_uid] = lambda: 'u1'
    return TestClient(app)


def test_send_a_conversation_shows_and_sends_exactly_the_preview(client, env):
    db, wiki = env
    _turn_on(wiki)
    preview = client.get('/v1/conversations/c-kept-1/send/preview').json()
    assert preview['title'] == 'Conversation - Plumber visit (edited) (c-kept-1)'
    assert '## Overview' in preview['content'] and '- [ ] Get $120 cash' in preview['content']
    assert '## Transcript' not in preview['content']  # include_transcript is off by default
    sent = client.post('/v1/conversations/c-kept-1/send', json={'target': 'wiki'}).json()
    assert sent['ok'] is True and sent['receipt']['revision'] == 0
    path = f"people/wizard/raw/sources/2026-11-16 - {preview['title']}.md"
    assert wiki.files[path] == preview['content']
    again = client.post('/v1/conversations/c-kept-1/send', json={'target': 'wiki'}).json()
    assert again == {'ok': False, 'receipt': None, 'error': 'Already sent to the wiki', 'already_sent': True}
    revised = client.post('/v1/conversations/c-kept-1/send', json={'target': 'wiki', 'revised': True}).json()
    assert revised['ok'] is True and revised['receipt']['title'].endswith('(revised 1)')
    assert len(client.get('/v1/conversations/c-kept-1/send/preview').json()['receipts']) == 2


def test_the_transcript_goes_only_when_the_user_chose_it(client, env):
    db, wiki = env
    _turn_on(wiki, include_transcript=True)
    preview = client.get('/v1/conversations/c-kept-1/send/preview').json()
    assert '## Transcript' in preview['content'] and '120 cash' in preview['content']


def test_send_is_refused_while_the_wiki_is_off(client, env):
    out = client.post('/v1/conversations/c-kept-1/send', json={'target': 'wiki'})
    assert out.status_code == 409 and 'Connections' in out.json()['detail']
    assert client.get('/v1/conversations/nope/send/preview').status_code in (404, 409)


@pytest.mark.asyncio
async def test_kept_clips_are_sent_when_ready_with_on_keep(env):
    db, wiki = env
    _turn_on(wiki, send_kept_conversations='ask')
    assert outbound_dispatch.conversations_kept('u1', ['c-kept-1'], now=APPROVED) == []  # only on_keep sends
    _turn_on(wiki, send_kept_conversations='on_keep')
    assert outbound_dispatch.conversations_kept('u1', ['c-kept-1'], now=APPROVED) == ['wiki_conv:u1:c-kept-1']
    out = await outbound_worker.run_due(APPROVED + timedelta(minutes=11))
    assert out == {'wiki_conv:u1:c-kept-1': 'sent'} and len(wiki.files) == 1


@pytest.mark.asyncio
async def test_a_clip_unkept_before_it_left_is_not_sent(env, monkeypatch):
    db, wiki = env
    _turn_on(wiki, send_kept_conversations='on_keep')
    outbound_dispatch.conversations_kept('u1', ['c-kept-1'], now=APPROVED)
    monkeypatch.setattr(
        conversations_db, 'get_conversation', lambda uid, cid, **k: {**CONVERSATIONS['c-kept-1'], 'starred': False}
    )
    out = await outbound_worker.run_due(APPROVED + timedelta(minutes=11))
    assert out == {'wiki_conv:u1:c-kept-1': 'dropped'} and wiki.files == {}


@pytest.mark.asyncio
async def test_orb_gets_a_short_log_line_when_turned_on(env):
    db, wiki = env
    orb = McpFake('orb-token', '/mcp')
    try:
        settings_db.set_target(
            'u1', 'orb', {'enabled': True, 'url': orb.url, 'log_day_approvals': True}, secrets={'token': 'orb-token'}
        )
        review = _approve({**REVIEW, 'title': 'A very long title for a Monday spent cutting fabric for the quilt'})
        assert outbound_dispatch.day_approved('u1', DATE, review, 'user', now=APPROVED) == [f'orb_day:u1:{DATE}']
        out = await outbound_worker.run_due(APPROVED + timedelta(minutes=11))
        assert out == {f'orb_day:u1:{DATE}': 'sent'}
        call = orb.calls[-1]
        assert call['name'] == 'orb_add_log' and call['arguments']['project'] == 'General'
        assert len(call['arguments']['text']) <= mcp_targets.ORB_LOG_MAX
        assert call['arguments']['text'].startswith('Omi day approved: A very long')
    finally:
        orb.close()


def test_auto_approve_marks_its_approvals_as_automatic(monkeypatch):
    seen = []
    monkeypatch.setattr(day_review_service, 'pending_dates', lambda uid, days: {'draft_dates': ['2026-11-15']})
    monkeypatch.setattr(day_review_service, 'approve', lambda uid, date, **k: seen.append(k) or {})
    assert auto_approve.approve_past_days('u1', '2026-11-17') == ['2026-11-15']
    assert seen == [{'approved_by': 'auto'}]


def test_a_settings_failure_never_undoes_the_approval(monkeypatch):
    def boom(uid):
        raise RuntimeError('firestore down')

    monkeypatch.setattr(outbound_dispatch.integrations_out, 'effective', boom)
    assert outbound_dispatch.day_approved('u1', DATE, REVIEW, 'user') == []
