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
    status_code = 200

    async def aiter_lines(self):
        for line in _SSE_LINES:
            yield line

    async def aread(self):
        return b""


class _FakeStreamContext:
    """Mimics httpx's `client.stream(...)` async context manager."""

    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        # This is the exact failure a closed client produced in production.
        if self._client.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return _FakeResponse()

    async def __aexit__(self, *exc_info):
        return False


class _FakeAsyncClient:
    """Records whether it was closed, so the test can detect premature close."""

    def __init__(self, *args, **kwargs):
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.closed = True
        return False

    def stream(self, *args, **kwargs):
        return _FakeStreamContext(self)

    async def post(self, *args, **kwargs):  # pragma: no cover - non-stream path
        raise AssertionError("non-streaming path must not run in this test")


def _patch_proxy_httpx():
    """Give merge_proxy a namespace whose AsyncClient is the fake, without
    disturbing the real httpx the test itself uses for the ASGI call."""
    fake_module = types.SimpleNamespace(
        **{name: getattr(httpx, name) for name in dir(httpx) if not name.startswith("_")}
    )
    fake_module.AsyncClient = _FakeAsyncClient
    merge_proxy.httpx = fake_module


async def _run() -> None:
    _patch_proxy_httpx()
    merge_proxy._upstreams["llama"] = "http://upstream.invalid/v1"

    transport = httpx.ASGITransport(app=merge_proxy.app)
    async with _RealAsyncClient(transport=transport, base_url="http://proxy.test") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "llama/test-model", "stream": True, "messages": []},
        ) as response:
            assert response.status_code == 200, f"unexpected status {response.status_code}"
            body = b"".join([chunk async for chunk in response.aiter_bytes()])

    text = body.decode("utf-8", errors="replace")
    assert "data:" in text, f"streamed body was empty/truncated: {text!r}"
    assert "[DONE]" in text, f"stream did not complete: {text!r}"


def test_streaming_chat_survives_the_handler_returning() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_streaming_chat_survives_the_handler_returning()
    print("PASS: streaming /v1/chat/completions delivers the full body")
