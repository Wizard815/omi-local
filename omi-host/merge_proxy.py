"""merge-proxy: auto-discovering multi-backend LLM proxy.

Queries every configured backend for its /v1/models, merges the lists, and
routes /v1/chat/completions to the right backend based on model name prefix.

Env:
  UPSTREAMS     — JSON map of tag → base_url
                   default: {"llama":"http://127.0.0.1:8081/v1"}
  OPENROUTER_API_KEY — if set, auto-adds OpenRouter as an 'or' upstream
  OPENAI_API_KEY     — fallback key for backends without their own
  REDIS_DB_HOST      — Redis host for allowlist sync (same as Omi backend)
  REDIS_DB_PORT      — Redis port (default 6379)
  REDIS_DB_PASSWORD  — Redis password (default empty)
  MERGE_PORT         — listen port (default 4000)
  MODEL_CACHE_TTL    — seconds to cache merged model list (default 60)
  MODEL_ALLOW        — comma-separated raw model IDs to show (env fallback;
                       overridden by Redis key omi:model:allow when set).
                   examples:
                     "gemma-4-12b,qwen3-8b"
                     "openai/gpt-4*"
                   Empty/not set = show everything.

Example dual-backend startup:
  UPSTREAMS='{"llama":"http://llama-cpp:8081/v1"}'
  OPENROUTER_API_KEY=sk-or-...
  MODEL_ALLOW=gemma-4-12b,qwen3-8b,gpt-4*,claude-sonnet*
  REDIS_DB_HOST=redis
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx
import redis as redis_lib
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

# ── config ──────────────────────────────────────────────────────────────

PORT = int(os.environ.get("MERGE_PORT", "4000"))
CACHE_TTL = float(os.environ.get("MODEL_CACHE_TTL", "60"))

_upstreams: dict[str, str] = json.loads(
    os.environ.get("UPSTREAMS", '{"llama":"http://127.0.0.1:8081/v1"}')
)

_or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
if _or_key:
    _upstreams["or"] = "https://openrouter.ai/api/v1"

DEFAULT_KEY = os.environ.get("OPENAI_API_KEY", "local").strip()

# ── Redis (optional — for dashboard-managed allowlist) ─────────────────
#
# Connected lazily, never pinged at import: in docker-compose.yml this
# container starts BEFORE omi-local (omi-local depends_on omi-proxy), and
# omi-local is what runs Redis. A ping-at-import failed on every cold start
# and left the proxy on the env allowlist forever, so the dashboard's
# "save allowlist" never reached it. redis-py connects on first command, and
# every read below is guarded, so an unreachable Redis just falls back to env.

_redis: Any = None
_redis_host = os.environ.get("REDIS_DB_HOST", "").strip()
if _redis_host:
    _redis = redis_lib.Redis(
        host=_redis_host,
        port=int(os.environ.get("REDIS_DB_PORT", "6379")),
        password=os.environ.get("REDIS_DB_PASSWORD", "") or None,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )

ALLOWLIST_REDIS_KEY = "omi:model:allow"

# ── model allowlist (lazy: Redis → env → show all) ─────────────────────


def _build_allowlist(raw: str) -> list[tuple[str, bool]]:
    """Parse MODEL_ALLOW into (prefix, is_prefix_match) tuples."""
    if not raw.strip():
        return []
    entries: list[tuple[str, bool]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if entry.endswith("*"):
            entries.append((entry[:-1], True))
        else:
            entries.append((entry, False))
    return entries


def _read_redis_allowlist() -> str | None:
    """The dashboard-saved allowlist, or None when Redis is unset/unreachable/empty."""
    if _redis is None:
        return None
    try:
        raw = _redis.get(ALLOWLIST_REDIS_KEY)
    except Exception:
        return None
    return raw.decode("utf-8") if raw else None


def _read_allowlist() -> str:
    """Read allowlist string: Redis → env → empty (show all)."""
    return _read_redis_allowlist() or os.environ.get("MODEL_ALLOW", "")


def _model_allowed(raw_id: str, allowlist: list[tuple[str, bool]] | None = None) -> bool:
    """Return True if this raw model ID passes the allowlist filter.

    Pass ``allowlist`` when filtering many models: reading it once per refresh
    instead of once per model keeps a slow Redis from multiplying into the
    model-list latency.
    """
    if allowlist is None:
        allowlist = _build_allowlist(_read_allowlist())
    if not allowlist:
        return True  # no filter = show everything
    return any(
        raw_id.startswith(prefix) if is_prefix else raw_id == prefix
        for prefix, is_prefix in allowlist
    )


def _flush_cache() -> None:
    """Force the next model list request to re-fetch from upstreams."""
    global _merged_models, _cache_expiry
    _merged_models = []
    _cache_expiry = 0.0


# ── model cache ─────────────────────────────────────────────────────────

_merged_models: list[dict] = []
_cache_expiry: float = 0.0
_cache_lock = asyncio.Lock()
# When every upstream came back empty (down, or still loading), don't ask
# again on each call: the dashboard and the chat shortcuts poll /v1/models,
# and each miss waited the full fetch timeout.
EMPTY_CACHE_TTL = float(os.environ.get("MODEL_EMPTY_CACHE_TTL", "5"))

# One client for every upstream call: keep-alive connections to llama.cpp
# instead of a TCP handshake per request. Created on the first use so import
# (and the tests) need no running event loop.
_client: httpx.AsyncClient | None = None


def _shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=600.0)
    return _client


def _tagged_name(tag: str, raw_id: str) -> str:
    return f"{tag}/{raw_id}"


def _untag(name: str) -> tuple[str, str]:
    if "/" in name:
        parts = name.split("/", 1)
        tag = parts[0]
        raw = parts[1]
        if tag in _upstreams:
            return tag, raw
    if _upstreams:
        default_tag = next(iter(_upstreams))
        return default_tag, name
    return "", name


async def _fetch_models(tag: str, base_url: str, allowlist: list[tuple[str, bool]]) -> list[dict]:
    try:
        headers: dict[str, str] = {}
        if tag == "or" and _or_key:
            headers["Authorization"] = f"Bearer {_or_key}"
        else:
            headers["Authorization"] = f"Bearer {DEFAULT_KEY}"

        resp = await _shared_client().get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=10.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
        models: list[dict] = []
        for m in data.get("data", []):
            raw_id = m.get("id", "unknown")
            if not _model_allowed(raw_id, allowlist):
                continue
            models.append({
                "id": _tagged_name(tag, raw_id),
                "owned_by": tag,
                "raw_id": raw_id,
            })
        return models
    except Exception:
        return []


async def refresh_models() -> list[dict]:
    global _merged_models, _cache_expiry

    async with _cache_lock:
        if time.monotonic() < _cache_expiry:
            return _merged_models

        allowlist = _build_allowlist(_read_allowlist())
        tasks = [_fetch_models(tag, url, allowlist) for tag, url in _upstreams.items()]
        results = await asyncio.gather(*tasks)

        merged: list[dict] = []
        for models in results:
            merged.extend(models)

        _merged_models = merged
        _cache_expiry = time.monotonic() + (CACHE_TTL if merged else EMPTY_CACHE_TTL)
        return _merged_models


# ── FastAPI app ─────────────────────────────────────────────────────────

app = FastAPI(title="merge-proxy", version="1.2.0")


@app.on_event("startup")
async def _warm_cache():
    await refresh_models()


@app.get("/health")
async def health():
    backends: dict[str, str] = {}
    for tag, url in _upstreams.items():
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{url.rstrip('/')}/models")
                backends[tag] = "up" if resp.status_code == 200 else f"status {resp.status_code}"
        except Exception as e:
            backends[tag] = str(e)

    redis_allow = _read_redis_allowlist()
    allow_raw = redis_allow or os.environ.get("MODEL_ALLOW", "")
    allowlist = _build_allowlist(allow_raw)

    return {
        "ok": True,
        "upstreams": backends,
        "model_count": len(_merged_models),
        "allowlist": [e[0] + ("*" if e[1] else "") for e in allowlist] if allowlist else "all",
        "allowlist_source": "redis" if redis_allow else ("env" if allow_raw else "none"),
    }


@app.get("/v1/models")
async def list_models():
    models = await refresh_models()
    return {
        "object": "list",
        "data": [{"id": m["id"], "owned_by": m["owned_by"]} for m in models],
    }


@app.post("/allowlist/reload")
async def reload_allowlist():
    """Called by the dashboard after saving a new allowlist.
    Flushes the model cache so the next /v1/models request re-fetches
    from upstreams with the new filter applied.
    """
    _flush_cache()
    return {"ok": True, "note": "Cache flushed — next /v1/models will re-filter"}


def _upstream_unreachable(tag: str, exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": f"upstream '{tag}' unreachable: {type(exc).__name__}", "type": "upstream_error"}},
        status_code=502,
    )


async def _post_upstream(tag: str, url: str, payload: dict, headers: dict[str, str]) -> Response:
    """Non-streaming POST that relays the upstream's status and body as-is.

    An unreachable upstream, or an error body that is not JSON (llama-swap and
    reverse proxies answer in plain text), used to raise inside the handler, so
    the caller got a bare "500 Internal Server Error" and the real reason — a
    model that failed to load, a backend that is down — was lost.
    """
    try:
        resp = await _shared_client().post(url, json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001 - relayed to the caller as a 502
        return _upstream_unreachable(tag, exc)
    try:
        content = json.loads(resp.content) if resp.content else {}
    except json.JSONDecodeError:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "text/plain"),
        )
    return JSONResponse(content=content, status_code=resp.status_code)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    model_name = payload.get("model", "")
    tag, raw_model = _untag(model_name)
    base_url = _upstreams.get(tag)
    if not base_url:
        return JSONResponse(
            {"error": f"unknown model '{model_name}' — no backend for tag '{tag}'"},
            status_code=400,
        )

    payload["model"] = raw_model

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if tag == "or" and _or_key:
        headers["Authorization"] = f"Bearer {_or_key}"
        payload.pop("stop", None)
    else:
        headers["Authorization"] = f"Bearer {DEFAULT_KEY}"

    stream = payload.get("stream", False)

    if stream:
        # The httpx client must outlive this handler: StreamingResponse iterates
        # the generator AFTER the handler returns, so a client opened in an
        # `async with` that wraps the `return` is already closed by the time the
        # generator runs — raising "Cannot send a request, as the client has
        # been closed." on the first chunk. The caller sees a truncated body
        # (RemoteProtocolError: incomplete chunked read) and falls back to a
        # canned reply. The generator owns closing the client instead.
        #
        # The upstream request is sent BEFORE returning, though, so an upstream
        # error keeps its real status: streaming the error JSON inside a 200
        # text/event-stream (the old behavior) made the OpenAI SDK fail to parse
        # it as SSE instead of raising APIStatusError, which is what the
        # backend's retry/fallback logic keys on.
        client = httpx.AsyncClient(timeout=600.0)
        try:
            upstream_request = client.build_request(
                "POST",
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp = await client.send(upstream_request, stream=True)
        except Exception as exc:
            await client.aclose()
            return _upstream_unreachable(tag, exc)

        if resp.status_code >= 400:
            body_bytes = await resp.aread()
            await resp.aclose()
            await client.aclose()
            return Response(
                content=body_bytes,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )

        async def _stream():
            try:
                async for line in resp.aiter_lines():
                    yield line + "\n"
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(_stream(), media_type="text/event-stream")

    return await _post_upstream(tag, f"{base_url.rstrip('/')}/chat/completions", payload, headers)


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    # Same tag-strip-and-route logic as /v1/chat/completions above — added
    # because this route didn't exist at all before, so any embedding model
    # picked from the local embedding-model dashboard (OPENAI_BASE_URL ->
    # this proxy) 404'd outright rather than reaching any backend.
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    model_name = payload.get("model", "")
    tag, raw_model = _untag(model_name)
    base_url = _upstreams.get(tag)
    if not base_url:
        return JSONResponse(
            {"error": f"unknown model '{model_name}' — no backend for tag '{tag}'"},
            status_code=400,
        )

    payload["model"] = raw_model

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if tag == "or" and _or_key:
        headers["Authorization"] = f"Bearer {_or_key}"
    else:
        headers["Authorization"] = f"Bearer {DEFAULT_KEY}"

    return await _post_upstream(tag, f"{base_url.rstrip('/')}/embeddings", payload, headers)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)