"""Self-hosted chat model shortcuts: "/adult <question>" answers with another model.

Seams: model_config's Redis/env lookups and chat_model_shortcuts' Redis hash are
patched; routers.chat.send_message runs for real with chat_db, quota and
execute_chat_stream replaced, so the test observes which model the answer
features would resolve to *inside* the streamed turn.
"""

import asyncio
import base64
import json

import pytest
from starlette.requests import Request

import routers.chat as chat_router
import utils.llm.chat_model_shortcuts as shortcuts
import utils.llm.model_config as model_config
from models.chat import SendMessageRequest
from utils.chat_session_target import ResolvedChatTarget


@pytest.fixture
def local_mode(monkeypatch):
    """Local-LLM deployment: proxy URL set, primary model picked in the dashboard."""
    runtime = {'primary': 'llama/gemma-4-12b'}
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://omi-proxy:4000/v1')
    monkeypatch.delenv('OMI_LOCAL_MODEL', raising=False)
    monkeypatch.delenv('LOCAL_LLM_MODEL', raising=False)
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: runtime.get(kind))
    return runtime


@pytest.fixture
def shortcut_store(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.delenv(shortcuts.SHORTCUTS_ENV, raising=False)
    monkeypatch.setattr(shortcuts.redis_db, 'get_chat_model_shortcuts', lambda: dict(store))
    monkeypatch.setattr(shortcuts.redis_db, 'set_chat_model_shortcut', lambda n, m: store.__setitem__(n, m))
    monkeypatch.setattr(shortcuts.redis_db, 'delete_chat_model_shortcut', lambda n: store.pop(n, None))
    return store


# ---------------------------------------------------------------- model_config


def test_turn_override_reroutes_only_answer_features(local_mode):
    token = model_config.set_chat_model_override('llama/big-70b')
    try:
        assert model_config.get_model('chat_agent') == 'llama/big-70b'
        assert model_config.get_model('chat_responses') == 'llama/big-70b'
        # Helper classifiers and background work stay on the base model.
        assert model_config.get_model('chat_extraction') == 'llama/gemma-4-12b'
        assert model_config.get_model('memories') == 'llama/gemma-4-12b'
    finally:
        model_config.reset_chat_model_override(token)
    assert model_config.get_model('chat_agent') == 'llama/gemma-4-12b'


def test_turn_override_is_ignored_outside_local_mode(monkeypatch):
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: None)
    token = model_config.set_chat_model_override('llama/big-70b')
    try:
        model, provider = model_config.get_model_config('chat_agent')
    finally:
        model_config.reset_chat_model_override(token)
    assert model != 'llama/big-70b'
    assert provider == 'openai'


# ------------------------------------------------------------- parsing/storage


def test_leading_shortcut_parsing():
    known = ['adult', 'code']
    parsed = shortcuts.parse_leading_shortcut('/adult  what did Sam say?\nmore', known)
    assert parsed == shortcuts.LeadingShortcut(name='adult', text='what did Sam say?\nmore')
    assert shortcuts.parse_leading_shortcut('/ADULT hi', known).name == 'adult'
    assert shortcuts.parse_leading_shortcut('/adult', known) is None  # nothing to answer
    assert shortcuts.parse_leading_shortcut('/etc/hosts is broken', known) is None
    assert shortcuts.parse_leading_shortcut('/unknown hi', known) is None
    assert shortcuts.parse_leading_shortcut('tell me /adult stuff', known) is None


def test_names_are_validated_and_cannot_shadow_app_commands(shortcut_store):
    assert shortcuts.set_shortcut('/Code', 'llama/coder') == 'code'
    assert shortcut_store == {'code': 'llama/coder'}
    for bad in ('model', 'clear', 'has space', '9lives', ''):
        with pytest.raises(shortcuts.InvalidShortcutName):
            shortcuts.set_shortcut(bad, 'llama/x')


def test_env_seed_is_overridden_by_saved_shortcuts(monkeypatch, shortcut_store):
    monkeypatch.setenv(shortcuts.SHORTCUTS_ENV, 'adult=llama/env-big, code=llama/env-coder')
    shortcut_store['adult'] = 'llama/dashboard-big'
    assert shortcuts.get_shortcuts() == {'adult': 'llama/dashboard-big', 'code': 'llama/env-coder'}
    monkeypatch.setenv(shortcuts.SHORTCUTS_ENV, '{"adult": "llama/json-big"}')
    shortcut_store.clear()
    assert shortcuts.resolve_shortcut_model('adult') == 'llama/json-big'


def test_resolve_turn_shortcut_prefers_the_explicit_field(shortcut_store):
    shortcut_store['adult'] = 'llama/big-70b'
    # Sticky toggle: the app sends the field and the plain text.
    assert shortcuts.resolve_turn_shortcut('hello', 'adult') == shortcuts.TurnShortcut(
        'adult', 'llama/big-70b', 'hello'
    )
    # A redundant typed prefix is stripped too.
    assert shortcuts.resolve_turn_shortcut('/adult hello', 'adult').text == 'hello'
    # Unconfigured default shortcut: named, no model.
    shortcut_store.clear()
    turn = shortcuts.resolve_turn_shortcut('/adult hello', None)
    assert (turn.name, turn.model) == ('adult', None)
    assert shortcuts.resolve_turn_shortcut('hello', None) == shortcuts.TurnShortcut(None, None, 'hello')


# ------------------------------------------------------------------ router


def _decode_done(frames: list[str]) -> dict:
    done = [f for f in frames if f.startswith('done: ')]
    assert done, frames
    return json.loads(base64.b64decode(done[-1][len('done: ') :].strip()))


@pytest.fixture
def chat_turn(monkeypatch, local_mode, shortcut_store):
    """Run one POST /v2/messages turn; record what the answer model would be."""
    persisted: list[dict] = []
    seen: dict = {}

    monkeypatch.setattr(chat_router, 'enforce_chat_quota', lambda *a, **k: None)
    monkeypatch.setattr(chat_router, 'resolve_chat_target', lambda uid, app_id, sid: ResolvedChatTarget(None, None))
    monkeypatch.setattr(chat_router, '_record_chat_quota_question', lambda *a, **k: None)
    monkeypatch.setattr(chat_router, 'try_acquire_goal_extraction_lock', lambda uid: False)
    monkeypatch.setattr(chat_router, 'get_available_app_by_id', lambda app_id, uid: None)
    monkeypatch.setattr(chat_router.chat_db, 'add_message', lambda uid, msg: persisted.append(msg))
    monkeypatch.setattr(
        chat_router.chat_db,
        'get_cache_aligned_messages',
        lambda uid, app_id=None, chat_session_id=None: list(reversed(persisted)),
    )

    async def _fake_execute_chat_stream(uid, messages, app, cited=False, callback_data=None, **kwargs):
        async def _producer():
            # A spawned producer task, like the agentic runner's.
            return model_config.get_model('chat_agent')

        seen['answer_model'] = await asyncio.create_task(_producer())
        seen['last_text'] = messages[-1].text
        callback_data['answer'] = 'ok'
        yield 'data: ok'
        yield None

    monkeypatch.setattr(chat_router, 'execute_chat_stream', _fake_execute_chat_stream)

    def _run(text: str, model_shortcut: str | None = None) -> list[str]:
        response = chat_router.send_message(
            SendMessageRequest(text=text, model_shortcut=model_shortcut),
            Request({'type': 'http', 'headers': [(b'x-app-platform', b'android')]}),
            plugin_id=None,
            app_id=None,
            chat_session_id=None,
            uid='uid-1',
        )

        async def _drain():
            out = []
            async for chunk in response.body_iterator:
                out.append(chunk if isinstance(chunk, str) else chunk.decode())
            return out

        return asyncio.run(_drain())

    return _run, persisted, seen


def test_typed_shortcut_answers_with_its_model_and_stores_clean_text(chat_turn, shortcut_store):
    run, persisted, seen = chat_turn
    shortcut_store['adult'] = 'llama/big-70b'

    run('/adult what did I promise Sam?')

    assert seen['answer_model'] == 'llama/big-70b'
    assert seen['last_text'] == 'what did I promise Sam?'
    assert persisted[0]['text'] == 'what did I promise Sam?'
    # The override does not leak past the turn.
    assert model_config.get_model('chat_agent') == 'llama/gemma-4-12b'


def test_plain_turn_uses_the_base_model(chat_turn, shortcut_store):
    run, _persisted, seen = chat_turn
    shortcut_store['adult'] = 'llama/big-70b'

    run('what did I promise Sam?')

    assert seen['answer_model'] == 'llama/gemma-4-12b'


def test_unconfigured_shortcut_explains_instead_of_answering(chat_turn):
    run, persisted, seen = chat_turn

    frames = run('hello', model_shortcut='adult')

    assert 'answer_model' not in seen  # no LLM turn ran
    assert persisted == []  # nothing stored
    assert '/adult model' in _decode_done(frames)['text']
