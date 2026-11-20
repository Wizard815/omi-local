"""Slow self-hosted models: one timeout setting and a reasoning-effort hint (config/local_llm.py).

A home LLM can take minutes per call. These tests pin that a self-hosted stack derives every
LLM deadline from OMI_LOCAL_LLM_TIMEOUT_SECONDS, that short structured calls carry the
configured reasoning effort, and that cloud deployments see none of it.
"""

import pytest

import utils.llm.model_config as model_config
from config import local_llm
from utils.llm import notes_agent_policy, providers
from utils.llm.clients import get_llm
from utils.other.timeout import TimeoutMiddleware
from utils.retrieval.agentic import stream_deadline_defaults


@pytest.fixture
def local_mode(monkeypatch):
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://omi-proxy:4000/v1')
    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.delenv(local_llm.TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(local_llm.REASONING_EFFORT_ENV, raising=False)
    monkeypatch.delenv('FAIR_USE_CLASSIFIER_MODEL', raising=False)
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: 'llama/slow-thinker' if kind == 'primary' else None)
    monkeypatch.setattr(providers, '_llm_cache', {})


@pytest.fixture
def cloud_mode(monkeypatch):
    monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    monkeypatch.delenv('OMI_SELF_HOSTED', raising=False)
    monkeypatch.delenv('OMI_LOCAL_MODEL', raising=False)
    monkeypatch.setenv(local_llm.TIMEOUT_ENV, '900')
    monkeypatch.setenv(local_llm.REASONING_EFFORT_ENV, 'low')
    monkeypatch.setattr(model_config, '_redis_model', lambda kind: None)


def test_timeout_defaults_to_five_minutes_when_self_hosted():
    env = {'OPENAI_BASE_URL': 'http://proxy/v1', 'OMI_SELF_HOSTED': '1'}
    assert local_llm.local_llm_timeout_seconds(env) == 300.0
    assert local_llm.local_http_timeout_seconds(env) == 900.0


def test_timeout_setting_is_read_and_floored():
    env = {'OPENAI_BASE_URL': 'http://proxy/v1', 'OMI_LOCAL_MODEL': 'llama/x'}
    assert local_llm.local_llm_timeout_seconds({**env, local_llm.TIMEOUT_ENV: '600'}) == 600.0
    assert local_llm.local_llm_timeout_seconds({**env, local_llm.TIMEOUT_ENV: '5'}) == local_llm.MIN_TIMEOUT_SECONDS
    assert local_llm.local_llm_timeout_seconds({**env, local_llm.TIMEOUT_ENV: 'soon'}) == 300.0


def test_cloud_has_no_local_timeout_or_effort():
    env = {local_llm.TIMEOUT_ENV: '900', local_llm.REASONING_EFFORT_ENV: 'low'}
    assert local_llm.local_llm_timeout_seconds(env) is None
    assert local_llm.local_http_timeout_seconds(env) is None
    assert local_llm.local_reasoning_effort(env) is None
    # A self-hosted stack on cloud keys (no OPENAI_BASE_URL) keeps cloud deadlines too.
    assert local_llm.local_llm_timeout_seconds({'OMI_SELF_HOSTED': '1'}) is None


def test_reasoning_effort_is_sent_only_when_configured():
    env = {'OPENAI_BASE_URL': 'http://proxy/v1', 'OMI_SELF_HOSTED': '1'}
    assert local_llm.local_reasoning_effort(env) is None
    assert local_llm.local_reasoning_effort({**env, local_llm.REASONING_EFFORT_ENV: 'LOW'}) == 'low'
    assert local_llm.local_reasoning_effort({**env, local_llm.REASONING_EFFORT_ENV: 'none'}) == 'none'
    assert local_llm.local_reasoning_effort({**env, local_llm.REASONING_EFFORT_ENV: 'turbo'}) is None


def test_every_local_feature_gets_the_local_deadline(local_mode, monkeypatch):
    monkeypatch.setenv(local_llm.TIMEOUT_ENV, '420')
    # A background feature with no cloud deadline, a foreground feature, and memory intake.
    assert model_config.feature_request_timeout('conv_folder') == 420.0
    assert model_config.feature_request_timeout('conv_structure') >= 420.0
    assert get_llm('conv_folder').request_timeout == 420.0
    # Memory intake passes request_timeout=60; a slow local model must not be cut at 60s.
    assert get_llm('memory_l2', request_timeout=60, max_retries=0).request_timeout == 420.0


def test_cloud_feature_deadlines_are_unchanged(cloud_mode):
    assert model_config.feature_request_timeout('conv_folder') is None
    assert model_config.feature_request_timeout('conv_structure') == model_config.FOREGROUND_REQUEST_TIMEOUT_SECONDS


def test_short_calls_carry_reasoning_effort_in_local_mode(local_mode, monkeypatch):
    monkeypatch.setenv(local_llm.REASONING_EFFORT_ENV, 'low')
    model, provider = model_config.get_model_config('conv_folder')
    options = model_config.get_route_options('conv_folder', model, provider)
    assert options['extra_body'] == {'reasoning_effort': 'low'}
    assert get_llm('chat_extraction').extra_body == {'reasoning_effort': 'low'}
    # Long generation (notes, memory extraction, chat answers) is left to the model.
    model, provider = model_config.get_model_config('conv_structure')
    assert 'extra_body' not in model_config.get_route_options('conv_structure', model, provider)


def test_no_reasoning_effort_unless_configured(local_mode):
    model, provider = model_config.get_model_config('conv_folder')
    assert 'extra_body' not in model_config.get_route_options('conv_folder', model, provider)


def test_cloud_routes_never_carry_reasoning_effort(cloud_mode):
    model, provider = model_config.get_model_config('conv_folder')
    extra_body = model_config.get_route_options('conv_folder', model, provider).get('extra_body') or {}
    assert 'reasoning_effort' not in extra_body


def test_fair_use_classifier_follows_the_local_model(local_mode, monkeypatch):
    assert model_config.get_model_config('fair_use') == ('llama/slow-thinker', 'openai')
    monkeypatch.setenv('FAIR_USE_CLASSIFIER_MODEL', 'llama/tiny-classifier')
    assert model_config.get_model_config('fair_use') == model_config.get_pinned_features()['fair_use']


def test_fair_use_classifier_stays_pinned_in_the_cloud(cloud_mode):
    assert model_config.get_model_config('fair_use') == model_config.get_pinned_features()['fair_use']


def test_http_limit_follows_the_local_model(local_mode, monkeypatch):
    monkeypatch.delenv('HTTP_DEFAULT_TIMEOUT', raising=False)
    monkeypatch.setenv(local_llm.TIMEOUT_ENV, '300')
    assert TimeoutMiddleware(app=lambda *a: None).default_timeout == 900.0
    monkeypatch.setenv('HTTP_DEFAULT_TIMEOUT', '200')
    assert TimeoutMiddleware(app=lambda *a: None).default_timeout == 200.0


def test_http_limit_unchanged_in_the_cloud(cloud_mode, monkeypatch):
    monkeypatch.delenv('HTTP_DEFAULT_TIMEOUT', raising=False)
    assert TimeoutMiddleware(app=lambda *a: None).default_timeout == 120.0


def test_notes_agent_deadline_fits_slow_calls(local_mode):
    budget = notes_agent_policy.budget_for_words(2000)
    assert budget.deadline_seconds == 900.0
    assert budget.force_submit_after_seconds == 540.0


def test_notes_agent_deadline_unchanged_in_the_cloud(cloud_mode):
    budget = notes_agent_policy.budget_for_words(2000)
    assert budget.deadline_seconds == notes_agent_policy.DEADLINE_SECONDS
    assert budget.force_submit_after_seconds == notes_agent_policy.FORCE_SUBMIT_AFTER_SECONDS


def test_chat_stream_deadlines_fit_a_slow_model():
    assert stream_deadline_defaults(None) == (25.0, 150.0)
    assert stream_deadline_defaults(300.0) == (300.0, 1200.0)
    assert stream_deadline_defaults(30.0) == (30.0, 150.0)
