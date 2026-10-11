"""Self-hosted device-calendar sync + chat tool.

Three defects from the original self-hosting change:
- the tool took `uid` as an LLM-visible argument ("injected automatically" —
  nothing injected it), so the model had to invent an id and never found events;
- events lived in a router-module dict, wiped by every backend restart;
- utils/retrieval/tools imported routers.device_calendar (utils must not import
  routers), which made every agentic-chat test suite that stubs auth crash on
  import.

Seam: database.redis_db.r is replaced by an in-memory fake.
"""

import ast
from pathlib import Path

import pytest

import database.device_calendar as device_calendar_db
import database.redis_db as redis_db
from utils.retrieval.tools.device_calendar_tools import get_device_calendar_events_tool

BACKEND = Path(__file__).resolve().parents[2]


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def set(self, key, value, ex=None):
        self.store[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ex

    def get(self, key):
        return self.store.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_db, 'r', fake)
    return fake


def _event(title, start_ms, end_ms):
    return {'title': title, 'start': 's', 'end': 'e', 'start_ms': start_ms, 'end_ms': end_ms}


def test_sync_survives_a_fresh_process_and_filters_by_window(fake_redis):
    device_calendar_db.store_events('uid-1', [_event('standup', 1_000, 2_000), _event('dinner', 10_000, 11_000)])

    # A new process has no module state; the snapshot is in Redis.
    assert [e['title'] for e in device_calendar_db.get_events('uid-1')] == ['standup', 'dinner']
    assert [e['title'] for e in device_calendar_db.get_events('uid-1', start_ms=5_000)] == ['dinner']
    assert device_calendar_db.has_events('uid-2') is False
    assert fake_redis.ttls['device_calendar:uid-1'] == device_calendar_db.SNAPSHOT_TTL_SECONDS


def test_tool_reads_the_user_from_run_config_not_from_the_model(fake_redis):
    from datetime import datetime, timedelta, timezone

    soon = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp() * 1000)
    device_calendar_db.store_events('uid-1', [_event('dentist', soon, soon + 3_600_000)])

    # The model never sees or supplies a uid.
    assert 'uid' not in get_device_calendar_events_tool.args

    out = get_device_calendar_events_tool.invoke({'days_ahead': 1}, config={'configurable': {'user_id': 'uid-1'}})
    assert 'dentist' in out

    other = get_device_calendar_events_tool.invoke({'days_ahead': 1}, config={'configurable': {'user_id': 'uid-2'}})
    assert 'No device calendar events' in other


def test_utils_layer_does_not_import_routers():
    """Static tripwire for the layering rule (database -> utils -> routers)."""
    source = (BACKEND / 'utils' / 'retrieval' / 'tools' / 'device_calendar_tools.py').read_text()
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any((name or '').startswith('routers') for name in imported)
