"""Local-LLM (self-hosted) model routing in utils/llm/model_config.py.

Seam: model_config._redis_model (the dashboard model picker's Redis lookups)
and the OPENAI_BASE_URL / OMI_LOCAL_MODEL / LOCAL_LLM_MODEL env vars.
"""

from unittest.mock import MagicMock

import pytest

import utils.llm.clients as clients
import utils.llm.model_config as model_config
from utils.llm import providers
from utils.llm.clients import get_llm
from utils.llm.gateway_client import LLM_GATEWAY_FEATURE_MODE_ENV_VAR, GatewayContextChatOpenAI


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


@pytest.fixture
def fresh_llm_clients(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.setattr(providers, '_llm_cache', {})


def test_local_mode_keeps_managed_features_off_the_omi_gateway(local_mode, fresh_llm_clients):
    """Upstream now sends every managed get_llm() through the Omi LLM gateway.

    A self-hosted stack has no gateway: local mode must keep generation on the
    direct OpenAI-compatible client at OPENAI_BASE_URL (the merge proxy).
    """
    llm = get_llm('conv_structure')

    assert not isinstance(llm, GatewayContextChatOpenAI)
    assert llm.model_name == 'llama/gemma-4-12b'
    assert str(llm.root_client.base_url).startswith('http://omi-proxy:4000/v1')


def test_hosted_mode_still_uses_the_gateway(monkeypatch, fresh_llm_clients):
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: None)

    assert isinstance(get_llm('conv_structure'), GatewayContextChatOpenAI)


def test_local_mode_embeddings_follow_the_dashboard_pick(local_mode, fresh_llm_clients):
    """Self-hosted: the embedding model is re-read per call, so a dashboard swap needs no restart."""
    proxy = clients._OpenAIEmbeddingsProxy('text-embedding-3-large', None, {})
    local_mode['embedding'] = 'llama/nomic-embed'

    first = proxy._default_client()
    assert first.model == 'llama/nomic-embed'
    assert proxy._default_client() is first

    local_mode['embedding'] = 'llama/bge-m3'
    assert proxy._default_client().model == 'llama/bge-m3'


def test_hosted_mode_embeddings_keep_the_constructed_client(monkeypatch):
    """Regression: the per-call re-resolution ran outside local mode too and replaced
    the configured client with a fresh text-embedding-3-large one."""
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.setenv('LOCAL_EMBEDDING_MODEL', 'llama/nomic-embed')
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: None)
    default = MagicMock()
    proxy = clients._OpenAIEmbeddingsProxy('text-embedding-3-small', default, {})

    assert proxy._default_client() is default


def test_local_mode_gemini_embed_query_without_a_key_uses_local_embeddings(local_mode, monkeypatch):
    """A self-hosted stack has no Gemini key: screen-activity search embeds locally, never via Google."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv(LLM_GATEWAY_FEATURE_MODE_ENV_VAR, raising=False)
    monkeypatch.setattr(clients, 'get_byok_key', lambda provider: None)
    monkeypatch.setattr(clients.httpx, 'post', MagicMock(side_effect=AssertionError('Google must not be called')))
    local_embeddings = MagicMock()
    local_embeddings.embed_query.return_value = [0.4, 0.5]
    monkeypatch.setattr(clients, 'embeddings', local_embeddings)

    assert clients.gemini_embed_query('standup notes') == [0.4, 0.5]
    local_embeddings.embed_query.assert_called_once_with('standup notes')
