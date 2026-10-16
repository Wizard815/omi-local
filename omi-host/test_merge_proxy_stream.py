#!/usr/bin/env python3
"""Regression test for the merge-proxy streaming client-lifetime bug.

The bug: /v1/chat/completions opened its httpx.AsyncClient in an `async with`
that wrapped the `return StreamingResponse(...)`. The `return` exits that block
immediately (the generator has not run yet), closing the client — so the first
chunk raised `RuntimeError: Cannot send a request, as the client has been
closed.` The caller (the Omi backend) saw a truncated body
(`RemoteProtocolError: incomplete chunked read`), retried, then fell back to a
canned reply — which is exactly what the phone showed when asking Omi a
question.

This test stubs httpx.AsyncClient so a *closed* client raises the same
RuntimeError, then drives the real ASGI app and asserts the streamed body
arrives intact. Under the old code it fails; under the fix it passes.

It also pins the non-streaming path (conversation processing, embeddings):
an unreachable upstream or a plain-text upstream error used to crash the
handler into a bare "500 Internal Server Error", hiding the real reason.

Run:  python3 test_merge_proxy_stream.py     (no pytest required)
"""

import asyncio
import os
import sys
import types

os.environ.setdefault("UPSTREAMS", '{"llama":"http://upstream.invalid/v1"}')
os.environ.setdefault("MERGE_PORT", "4000")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import httpx
except ImportError:  # pragma: no cover
    print("SKIP: httpx not installed")
    sys.exit(0)

# merge_proxy imports redis unconditionally, but only *uses* it when
# REDIS_DB_HOST is set (which this test never sets). Stub the module so the
# import succeeds on a machine without redis-py installed.
if "redis" not in sys.modules:
    try:
        import redis  # noqa: F401
    except ImportError:
        _redis_stub = types.ModuleType("redis")

        class _UnusedRedis:  # pragma: no cover - never constructed here
            def __init__(self, *args, **kwargs):
                raise RuntimeError("redis stub: Redis() must not be constructed in this test")

        _redis_stub.Redis = _UnusedRedis
        sys.modules["redis"] = _redis_stub

try:
    import merge_proxy
except ImportError as exc:  # pragma: no cover
    print(f"SKIP: cannot import merge_proxy ({exc})")
    sys.exit(0)

_RealAsyncClient = httpx.AsyncClient

# ── fake upstream plumbing ──────────────────────────────────────────────

_SSE_LINES = ['data: {"choices":[{"delta":{"content":"hi"}}]}', "data: [DONE]"]


class _FakeResponse:
    def __init__(self, client, status_code=200, body=b""):
        self._client = client
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": "application/json"}

    async def aiter_lines(self):
        for line in _SSE_LINES:
            # This is the exact failure a closed client produced in production.
            if self._client.closed:
                raise RuntimeError("Cannot send a request, as the client has been closed.")
            yield line

    async def aread(self):
        return self._body

    async def aclose(self):
        return None


class _FakeAsyncClient:
    """Records whether it was closed, so the test can detect premature close."""

    upstream_status = 200
    upstream_body = b""
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.closed = True
        return False

    async def aclose(self):
        self.closed = True

    def build_request(self, *args, **kwargs):
        return (args, kwargs)

    async def send(self, _request, stream=False):
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return _FakeResponse(self, _FakeAsyncClient.upstream_status, _FakeAsyncClient.upstream_body)

    async def post(self, *args, **kwargs):  # pragma: no cover - non-stream path
        raise AssertionError("non-streaming path must not run in this test")


class _FakePlainResponse:
    def __init__(self, status_code, content, content_type):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


class _NonStreamingClient:
    """Fake for the non-streaming path: post() raises or returns `outcome`."""

    outcome = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, *args, **kwargs):
        if isinstance(_NonStreamingClient.outcome, Exception):
            raise _NonStreamingClient.outcome
        return _NonStreamingClient.outcome


def _patch_proxy_httpx(client_cls=_FakeAsyncClient):
    """Give merge_proxy a namespace whose AsyncClient is the fake, without
    disturbing the real httpx the test itself uses for the ASGI call."""
    fake_module = types.SimpleNamespace(
        **{name: getattr(httpx, name) for name in dir(httpx) if not name.startswith("_")}
    )
    fake_module.AsyncClient = client_cls
    merge_proxy.httpx = fake_module


async def _stream_chat(client):
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "llama/test-model", "stream": True, "messages": []},
    ) as response:
        body = b"".join([chunk async for chunk in response.aiter_bytes()])
    return response.status_code, body


async def _run() -> None:
    _patch_proxy_httpx()
    merge_proxy._upstreams["llama"] = "http://upstream.invalid/v1"
    _FakeAsyncClient.upstream_status, _FakeAsyncClient.upstream_body = 200, b""
    _FakeAsyncClient.instances.clear()

    transport = httpx.ASGITransport(app=merge_proxy.app)
    async with _RealAsyncClient(transport=transport, base_url="http://proxy.test") as client:
        status, body = await _stream_chat(client)

    assert status == 200, f"unexpected status {status}"
    text = body.decode("utf-8", errors="replace")
    assert "data:" in text, f"streamed body was empty/truncated: {text!r}"
    assert "[DONE]" in text, f"stream did not complete: {text!r}"
    assert all(c.closed for c in _FakeAsyncClient.instances), "upstream client leaked after the stream ended"


async def _run_upstream_error() -> None:
    _patch_proxy_httpx()
    merge_proxy._upstreams["llama"] = "http://upstream.invalid/v1"
    _FakeAsyncClient.upstream_status = 400
    _FakeAsyncClient.upstream_body = b'{"error":{"message":"model not loaded"}}'
    _FakeAsyncClient.instances.clear()
    try:
        transport = httpx.ASGITransport(app=merge_proxy.app)
        async with _RealAsyncClient(transport=transport, base_url="http://proxy.test") as client:
            status, body = await _stream_chat(client)
    finally:
        _FakeAsyncClient.upstream_status, _FakeAsyncClient.upstream_body = 200, b""

    # The OpenAI SDK raises APIStatusError (which the backend retries/falls
    # back on) only if the error keeps its real HTTP status — a 200 SSE
    # response wrapping the error JSON fails as an SSE parse error instead.
    assert status == 400, f"upstream error was masked as {status}"
    assert b"model not loaded" in body
    assert all(c.closed for c in _FakeAsyncClient.instances), "upstream client leaked on the error path"


async def _post_non_streaming(path: str, outcome) -> "httpx.Response":
    _patch_proxy_httpx(_NonStreamingClient)
    merge_proxy._upstreams["llama"] = "http://upstream.invalid/v1"
    _NonStreamingClient.outcome = outcome
    transport = httpx.ASGITransport(app=merge_proxy.app, raise_app_exceptions=False)
    async with _RealAsyncClient(transport=transport, base_url="http://proxy.test") as client:
        return await client.post(path, json={"model": "llama/test-model", "messages": [], "input": "x"})


def test_non_streaming_unreachable_upstream_is_a_502_with_the_reason() -> None:
    response = asyncio.run(_post_non_streaming("/v1/chat/completions", httpx.ConnectError("connection refused")))
    assert response.status_code == 502, f"masked as {response.status_code}: {response.text!r}"
    assert "upstream 'llama' unreachable: ConnectError" in response.text, response.text


def test_non_streaming_plain_text_upstream_error_keeps_status_and_text() -> None:
    for path in ("/v1/chat/completions", "/v1/embeddings"):
        upstream = _FakePlainResponse(503, b"unable to start process: out of memory", "text/plain")
        response = asyncio.run(_post_non_streaming(path, upstream))
        assert response.status_code == 503, f"{path}: masked as {response.status_code}: {response.text!r}"
        assert response.text == "unable to start process: out of memory", f"{path}: {response.text!r}"


def test_streaming_chat_survives_the_handler_returning() -> None:
    asyncio.run(_run())


def test_streaming_upstream_error_keeps_its_status() -> None:
    asyncio.run(_run_upstream_error())


def test_unreachable_redis_falls_back_to_env_allowlist() -> None:
    """Redis down at startup must not disable the dashboard allowlist forever.

    The proxy starts before omi-local (which hosts Redis), so the old
    ping-at-import always failed and pinned the proxy to MODEL_ALLOW. Reads are
    now per-refresh and guarded: a failing Redis falls back to env, and a
    Redis that comes up later is honored without a restart.
    """

    class _FlakyRedis:
        def __init__(self):
            self.value = None
            self.down = True

        def get(self, _key):
            if self.down:
                raise ConnectionError("redis not up yet")
            return self.value

    flaky = _FlakyRedis()
    original_redis = merge_proxy._redis
    original_env = os.environ.get("MODEL_ALLOW")
    merge_proxy._redis = flaky
    os.environ["MODEL_ALLOW"] = "env-model"
    try:
        assert merge_proxy._read_allowlist() == "env-model"
        flaky.down = False
        flaky.value = b"gemma-*"
        assert merge_proxy._read_allowlist() == "gemma-*"
        allow = merge_proxy._build_allowlist(merge_proxy._read_allowlist())
        assert merge_proxy._model_allowed("gemma-4-12b", allow)
        assert not merge_proxy._model_allowed("env-model", allow)
    finally:
        merge_proxy._redis = original_redis
        if original_env is None:
            os.environ.pop("MODEL_ALLOW", None)
        else:
            os.environ["MODEL_ALLOW"] = original_env


def test_redis_down_at_import_does_not_pin_env_allowlist() -> None:
    """Import with REDIS_DB_HOST set while Redis is unreachable (compose start order).

    The old module pinged at import and set _redis = None on failure, so a
    proxy started before omi-local's Redis ignored the dashboard allowlist
    until restarted.
    """
    import importlib

    import redis as redis_module

    class _DownAtStartRedis:
        state = {"down": True, "value": b"qwen*"}

        def __init__(self, *args, **kwargs):
            pass

        def ping(self):
            raise ConnectionError("redis not up yet")

        def get(self, _key):
            if _DownAtStartRedis.state["down"]:
                raise ConnectionError("redis not up yet")
            return _DownAtStartRedis.state["value"]

    original_cls = redis_module.Redis
    original_host = os.environ.get("REDIS_DB_HOST")
    redis_module.Redis = _DownAtStartRedis
    os.environ["REDIS_DB_HOST"] = "omi-local"
    try:
        reloaded = importlib.reload(merge_proxy)
        assert reloaded._read_allowlist() == os.environ.get("MODEL_ALLOW", "")
        _DownAtStartRedis.state["down"] = False  # omi-local finished booting
        assert reloaded._read_allowlist() == "qwen*", "dashboard allowlist ignored after Redis came up"
    finally:
        redis_module.Redis = original_cls
        if original_host is None:
            os.environ.pop("REDIS_DB_HOST", None)
        else:
            os.environ["REDIS_DB_HOST"] = original_host
        importlib.reload(merge_proxy)


if __name__ == "__main__":
    test_redis_down_at_import_does_not_pin_env_allowlist()
    test_streaming_chat_survives_the_handler_returning()
    test_streaming_upstream_error_keeps_its_status()
    test_unreachable_redis_falls_back_to_env_allowlist()
    test_non_streaming_unreachable_upstream_is_a_502_with_the_reason()
    test_non_streaming_plain_text_upstream_error_keeps_status_and_text()
    print("PASS: merge proxy streaming + non-streaming errors + allowlist fallback")
