"""local-llm-adapter: Anthropic Messages API -> OpenAI-compatible (llama.cpp).

Lets Omi's agentic chat (Anthropic SDK, model claude-sonnet-4-6) run on a
local OpenAI-compatible server (llama.cpp on the MI50s) with ZERO backend
code changes: the backend just gets ANTHROPIC_BASE_URL=http://<host>:8788.

Env:
  OPENAI_BASE_URL   OpenAI-compatible server to forward to (default http://127.0.0.1:8081/v1)
  OPENAI_API_KEY    key for that server (default "local")
  LOCAL_LLM_MODEL   model name sent upstream; every requested Anthropic model
                    name (claude-sonnet-4-6, ...) is rewritten to this
                    (default: whatever llama.cpp serves; use llama.cpp --alias
                    or set this to your GGUF's registered name)
  LLM_ADAPTER_PORT  listen port for the Anthropic surface (default 8788)
"""
from __future__ import annotations
import json, os, uuid
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8081/v1").rstrip("/")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "local")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "").strip()
PORT = int(os.environ.get("LLM_ADAPTER_PORT", "8788"))

app = FastAPI(title="local-llm-adapter")
http: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _up():
    global http
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(900.0, connect=15.0),
        headers={"Authorization": f"Bearer {OPENAI_KEY}"})


# ---------------------------------------------------------------- conversion

def _flatten_content(content) -> str:
    """Anthropic content blocks (or list of blocks) -> plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text")
    return "" if content is None else str(content)


def _conv_messages(req):
    """Anthropic messages -> OpenAI chat messages.

    Handles: text blocks, assistant tool_use blocks, and USER messages that
    carry tool_result blocks (Anthropic returns tool results as role=user).
    """
    out = []
    sys = req.get("system")
    if sys:
        txt = sys if isinstance(sys, str) else "".join(
            b.get("text", "") for b in sys
            if isinstance(b, dict) and b.get("type") == "text")
        if txt:
            out.append({"role": "system", "content": txt})

    for m in req.get("messages", []):
        role = m.get("role")
        content = m.get("content")

        if role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            text, calls = [], []
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    text.append(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    calls.append({
                        "id": b.get("id") or ("call_" + str(uuid.uuid4())[:8]),
                        "type": "function",
                        "function": {
                            "name": b.get("name", ""),
                            "arguments": json.dumps(b.get("input", {}) or {}),
                        }})
            msg = {"role": "assistant", "content": "".join(text) or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)

        elif role == "user":
            # split user message into tool_result blocks vs regular content
            results, parts = [], []
            for b in (content if isinstance(content, list) else []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    r = b.get("content")
                    if isinstance(r, list):
                        r = "".join(x.get("text", "") for x in r
                                    if isinstance(x, dict))
                    results.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id") or "",
                        "content": "" if r is None else str(r)})
                elif b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "thinking":
                    continue  # drop reasoning blocks
            if results:
                out.extend(results)
            if parts or isinstance(content, str):
                txt = "".join(parts) if parts else (
                    content if isinstance(content, str) else "")
                if txt:
                    out.append({"role": "user", "content": txt})

        elif role == "tool":  # defensive: OpenAI-style already
            out.append({"role": "tool",
                        "tool_call_id": m.get("tool_call_id") or "",
                        "content": _flatten_content(content)})
    return out


def _conv_tools(req):
    tools = []
    for t in req.get("tools") or []:
        if not isinstance(t, dict):
            continue
        tools.append({"type": "function", "function": {
            "name": t.get("name", ""),
            "description": t.get("description", "") or "",
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}})
    return tools


def _tool_choice(tc):
    if not tc:
        return None
    if isinstance(tc, str):
        return tc if tc in ("auto", "none", "required") else None
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "none":
        return "none"
    if t == "tool" and tc.get("name"):
        return {"type": "function", "function": {"name": tc["name"]}}
    return None


def _build_openai_payload(req):
    payload = {
        "model": LOCAL_MODEL or req.get("model") or "local",
        "messages": _conv_messages(req),
        "max_tokens": req.get("max_tokens") or 4096,
        "temperature": req.get("temperature", 0.7),
        "stream": bool(req.get("stream", False)),
    }
    top_p = req.get("top_p")
    if top_p is not None:
        payload["top_p"] = top_p
    if req.get("stop_sequences"):
        payload["stop"] = req["stop_sequences"]
    tools = _conv_tools(req)
    if tools:
        payload["tools"] = tools
        tc = _tool_choice(req.get("tool_choice"))
        if tc:
            payload["tool_choice"] = tc
    return payload


def _stop_reason(finish, has_tool_use):
    if has_tool_use:
        return "tool_use"
    return {"stop": "end_turn", "length": "max_tokens",
            "tool_calls": "tool_use", "content_filter": "end_turn"}.get(
                finish or "", "end_turn")


def _conv_response(data, model):
    """OpenAI chat.completion -> Anthropic message object."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    content = []
    text = msg.get("content")
    if text:
        content.append({"type": "text", "text": text})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        try:
            args = json.loads(args) if args else {}
        except Exception:
            args = {"_raw": args}
        content.append({
            "type": "tool_use",
            "id": tc.get("id") or ("toolu_" + str(uuid.uuid4())[:12]),
            "name": fn.get("name", ""), "input": args})
    return {
        "id": "msg_" + str(uuid.uuid4())[:12], "type": "message",
        "role": "assistant", "model": model, "content": content,
        "stop_reason": _stop_reason(choice.get("finish_reason"), bool(content) and content[-1].get("type") == "tool_use"),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": usage.get("completion_tokens", 0)},
    }


# ------------------------------------------------------------------ streaming

def _sse(event, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_sse(req):
    payload = _build_openai_payload(req)
    model = payload["model"]
    msg_id = "msg_" + str(uuid.uuid4())[:12]

    async def gen():
        yield _sse("message_start", {"type": "message_start", "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "content": [], "model": model, "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}})
        yield _sse("ping", {"type": "ping"})
        yield _sse("content_block_start", {"type": "content_block_start",
                   "index": 0, "content_block": {"type": "text", "text": ""}})

        text_open = True       # is the text block (index 0) still open?
        tool_blocks = {}       # openai tool index -> anthropic block index
        next_block = 1         # next anthropic block index after text(0)
        finish_reason = None
        has_tool = False

        async with http.stream("POST", OPENAI_BASE + "/chat/completions",
                               json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except Exception:
                    continue
                ch = (chunk.get("choices") or [{}])[0]
                if ch.get("finish_reason"):
                    finish_reason = ch["finish_reason"]
                delta = ch.get("delta") or {}

                if delta.get("content"):
                    if not text_open:
                        # text arriving after tools — reopen a new text block
                        text_open = True
                        block = next_block
                        next_block += 1
                        yield _sse("content_block_start", {
                            "type": "content_block_start", "index": block,
                            "content_block": {"type": "text", "text": ""}})
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta",
                                  "text": delta["content"]}})

                for tc in delta.get("tool_calls") or []:
                    if text_open:
                        yield _sse("content_block_stop",
                                   {"type": "content_block_stop", "index": 0})
                        text_open = False
                    oi = tc.get("index", 0)
                    block = tool_blocks.get(oi)
                    if block is None:
                        # first fragment of this tool call: open its block
                        block = next_block
                        next_block += 1
                        tool_blocks[oi] = block
                        has_tool = True
                        fn = tc.get("function") or {}
                        yield _sse("content_block_start", {
                            "type": "content_block_start", "index": block,
                            "content_block": {
                                "type": "tool_use",
                                "id": tc.get("id") or ("toolu_" + str(uuid.uuid4())[:12]),
                                "name": fn.get("name", ""), "input": {}}})
                    fn = tc.get("function") or {}
                    if fn.get("name") and block is not None:
                        # late name arrival (some servers) — ignore, already sent
                        pass
                    args = fn.get("arguments")
                    if args:
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta", "index": block,
                            "delta": {"type": "input_json_delta",
                                      "partial_json": args}})

        # close open blocks
        if text_open:
            yield _sse("content_block_stop",
                       {"type": "content_block_stop", "index": 0})
        for block in sorted(tool_blocks.values()):
            yield _sse("content_block_stop",
                       {"type": "content_block_stop", "index": block})
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": _stop_reason(finish_reason, has_tool),
                      "stop_sequence": None},
            "usage": {"output_tokens": 0}})
        yield _sse("message_stop", {"type": "message_stop"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------- routes

@app.get("/health")
async def health():
    return {"ok": True, "upstream": OPENAI_BASE, "model": LOCAL_MODEL or "pass-through"}


@app.post("/v1/messages")
async def messages(request: Request):
    try:
        req = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {
            "type": "invalid_request_error", "message": "invalid JSON"}},
            status_code=400)
    if req.get("stream"):
        return await _stream_sse(req)
    try:
        resp = await http.post(OPENAI_BASE + "/chat/completions",
                               json=_build_openai_payload(req))
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        return JSONResponse({"type": "error", "error": {
            "type": "api_error",
            "message": f"upstream {e.response.status_code}: {e.response.text[:500]}"}},
            status_code=502)
    except Exception as e:
        return JSONResponse({"type": "error", "error": {
            "type": "api_error", "message": f"upstream unreachable: {e}"}},
            status_code=502)
    return JSONResponse(_conv_response(data, req.get("model") or "local"))


@app.get("/v1/models")
async def models():
    return {"data": [{"id": LOCAL_MODEL or "local", "object": "model"}]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
