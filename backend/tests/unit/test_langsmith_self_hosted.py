"""omi-local: LangSmith tracing and prompt fetches never run on a self-hosted server."""

from utils.observability import langsmith


def test_self_hosted_ignores_langsmith_keys_and_tracing(monkeypatch):
    monkeypatch.setenv('LANGSMITH_API_KEY', 'lsv2_real_looking_key')
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.delenv('OMI_SELF_HOSTED', raising=False)
    assert langsmith.has_langsmith_api_key() and langsmith.is_langsmith_enabled()

    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    assert not langsmith.has_langsmith_api_key()
    assert not langsmith.is_langsmith_enabled()
    assert langsmith.get_chat_tracer_callbacks(run_id='r') in (None, [])
    assert langsmith.submit_langsmith_feedback(run_id='r', score=1.0) is False
