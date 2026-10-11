"""Local-LLM (self-hosted) model routing in utils/llm/model_config.py.

Seam: model_config._redis_model (the dashboard model picker's Redis lookups)
and the OPENAI_BASE_URL / OMI_LOCAL_MODEL / LOCAL_LLM_MODEL env vars.
"""

import pytest

import utils.llm.model_config as model_config


@pytest.fixture
def local_mode(monkeypatch):
    """Local-LLM deployment: proxy URL set, primary model picked in the dashboard."""
    runtime = {'primary': 'llama/gemma-4-12b'}
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://omi-proxy:4000/v1')
    monkeypatch.delenv('OMI_LOCAL_MODEL', raising=False)
    monkeypatch.delenv('LOCAL_LLM_MODEL', raising=False)
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: runtime.get(kind))
    return runtime


def test_chat_agent_without_its_own_model_uses_primary(local_mode):
    """Regression: an unset chat model sent model="" to the proxy and Ask Omi only apologized."""
    assert model_config.get_model_config('chat_agent') == ('llama/gemma-4-12b', 'openai')

    local_mode['chat_agent'] = 'llama/qwen3-8b'
    assert model_config.get_model_config('chat_agent') == ('llama/qwen3-8b', 'openai')


def test_dashboard_pick_wins_over_env_default(local_mode, monkeypatch):
    monkeypatch.setenv('OMI_LOCAL_MODEL', 'llama/env-default')
    assert model_config.get_model('memories') == 'llama/gemma-4-12b'
    local_mode.pop('primary')
    assert model_config.get_model('memories') == 'llama/env-default'


def test_is_local_mode_counts_a_dashboard_picked_model(local_mode):
    """The device-calendar chat tool was gated on OMI_LOCAL_MODEL alone, missing dashboard picks."""
    assert model_config.is_local_mode() is True


def test_not_local_without_a_proxy_url(monkeypatch):
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: 'llama/gemma-4-12b')
    assert model_config.is_local_mode() is False
    assert model_config.get_model_config('chat_agent')[0] != 'llama/gemma-4-12b'
