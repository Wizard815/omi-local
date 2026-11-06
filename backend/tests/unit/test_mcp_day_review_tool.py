"""The get_day_review MCP tool (omi-local): the day bundle, compact, through the shared registry."""

import os
from datetime import datetime, timezone

os.environ.setdefault('ENCRYPTION_SECRET', 'omi_ZwB2ZNqB2HHpMK6wStk7sTpavJiPTFg7gXUHnc4tFABPU6pZ2c2DKgehtfgi4RZv')

import pytest  # noqa: E402

from utils import day_review_service  # noqa: E402
from utils.mcp_server.errors import ToolExecutionError  # noqa: E402
from utils.mcp_server.handlers import other  # noqa: E402
from utils.mcp_server.registry import TOOL_REQUIRED_SCOPE, spec_for_tool  # noqa: E402

DAY = {
    'date': '2026-10-27',
    'review': {
        'title': 'A quiet Tuesday',
        'final_text': 'You had coffee.',
        'tags': ['coffee'],
        'sections': [
            {
                'id': 's1',
                'title': 'Coffee',
                'text': 'You had coffee.',
                'tags': ['coffee'],
                'conversation_ids': ['c1'],
                'favorited': False,
            }
        ],
        'kept_conversation_ids': ['c2'],
        'rolled_up_conversation_ids': ['c1'],
        'generated_at': datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc),
        'generation_count': 1,
        'corrections': [],
        'summary_text': 'secret internal field',
    },
    'conversations': [
        {
            'id': 'c1',
            'title': 'Coffee',
            'overview': 'o',
            'started_at': None,
            'finished_at': None,
            'starred': False,
            'tags': ['coffee'],
            'rolled_up': True,
            'dropped': False,
            'held': False,
            'transcript_snippet': 'not exposed',
        },
        {
            'id': 'c2',
            'title': 'Standup',
            'overview': 'o',
            'started_at': None,
            'finished_at': None,
            'starred': True,
            'tags': [],
            'rolled_up': False,
            'dropped': False,
            'held': False,
        },
    ],
    'processing_count': 0,
    'hold_enabled': True,
}


def test_registered_under_conversations_read():
    spec = spec_for_tool('get_day_review')
    assert spec is not None and spec.scope == 'conversations.read' == TOOL_REQUIRED_SCOPE['get_day_review']
    assert spec.input_schema['properties']['date']['type'] == 'string'


def test_a_bundled_day_is_compact_and_a_missing_date_means_today(monkeypatch):
    seen = {}
    monkeypatch.setattr(day_review_service, 'get_day', lambda uid, date: seen.update(date=date) or DAY)
    import database.notifications as notification_db

    monkeypatch.setattr(notification_db, 'get_user_time_zone', lambda uid: 'UTC')
    out = other.get_day_review('u1', {})
    assert seen['date'] == day_review_service.local_today('UTC').isoformat()
    assert out['bundled'] is True and out['review']['title'] == 'A quiet Tuesday'
    assert out['review']['sections'][0]['conversation_ids'] == ['c1']
    assert 'summary_text' not in out['review'] and 'transcript_snippet' not in out['conversations'][0]
    assert out['conversations'][0]['in_bundle'] is True and out['conversations'][1]['starred'] is True


def test_errors_become_tool_errors(monkeypatch):
    def boom(uid, date):
        raise day_review_service.DayReviewError(422, 'Date cannot be in the future')

    monkeypatch.setattr(day_review_service, 'get_day', boom)
    with pytest.raises(ToolExecutionError) as caught:
        other.get_day_review('u1', {'date': '2999-01-01'})
    assert 'future' in str(caught.value)
