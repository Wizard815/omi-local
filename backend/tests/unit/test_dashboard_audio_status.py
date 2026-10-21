"""Dashboard audio-service status follows omi-host's OMI_AUDIO_URL routing.

omi-host/audio-env.sh leaves HOSTED_PARAKEET_API_URL empty when the operator
turns local audio off (OMI_AUDIO_URL=off, cloud STT only). That is a chosen
configuration, not an outage, so the dashboard must not probe "/health" and
count the stack as degraded.
"""

import asyncio
import importlib

import pytest


@pytest.fixture
def dashboard(monkeypatch):
    module = importlib.import_module("routers.dashboard")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_VECTOR_DB", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    return module


def _probe(module, monkeypatch, healthy=True):
    probed = []

    async def _check_http(url, timeout=5.0):
        probed.append(url)
        return (True, 200, "") if healthy else (False, None, "connection refused")

    monkeypatch.setattr(module, "_check_http", _check_http)
    return probed


def test_audio_off_is_not_configured_and_not_probed(dashboard, monkeypatch):
    monkeypatch.setenv("HOSTED_PARAKEET_API_URL", "")
    probed = _probe(dashboard, monkeypatch)

    status = asyncio.run(dashboard._get_async_components())["audio_service"]

    assert status["status"] == "not_configured"
    assert probed == []


def test_audio_url_is_probed_at_its_health_endpoint(dashboard, monkeypatch):
    monkeypatch.setenv("HOSTED_PARAKEET_API_URL", "http://192.168.20.10:8790/")
    probed = _probe(dashboard, monkeypatch, healthy=False)

    status = asyncio.run(dashboard._get_async_components())["audio_service"]

    assert probed == ["http://192.168.20.10:8790/health"]
    assert status["status"] == "down"
