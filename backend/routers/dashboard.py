"""Web dashboard for the self-hosted Omi stack.

Serves a health/telemetry page at /, component status, and
a runtime model picker powered by LiteLLM.
"""
import os
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter()

STARTED_AT = time.time()

# ── model selection request body ──────────────────────────────────────


class ModelSelectRequest(BaseModel):
    primary: str = Field(default="", description="Primary model (memories, summaries, features)")
    chat_agent: str = Field(default="", description="Chat agent model (conversations, tool use)")
    embedding: str = Field(default="", description="Embedding model (memory/vector search)")


class ModelAllowRequest(BaseModel):
    allowlist: str = Field(default="", description="Comma-separated model patterns (e.g. 'gemma-4-12b,gpt-4*,claude-sonnet*')")


# ── helpers ───────────────────────────────────────────────────────────


def _check_tcp(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "reachable"
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        return False, str(e)


async def _check_http(url: str, timeout: float = 5.0) -> tuple[bool, int | None, str]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return True, resp.status_code, ""
    except Exception as e:
        return False, None, str(e)


def _get_component_status() -> dict:
    results = {}

    redis_host = os.getenv("REDIS_DB_HOST", "127.0.0.1")
    redis_port = int(os.getenv("REDIS_DB_PORT", "6380"))
    ok, err = _check_tcp(redis_host, redis_port)
    results["redis"] = {"status": "up" if ok else "down", "detail": err if not ok else f"{redis_host}:{redis_port}"}

    fs_host = os.getenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8085")
    ok, err = _check_tcp(fs_host.split(":")[0], int(fs_host.split(":")[1]))
    results["firestore_emulator"] = {"status": "up" if ok else "down", "detail": err if not ok else fs_host}

    ok, err = _check_tcp("127.0.0.1", 9099)
    results["auth_emulator"] = {"status": "up" if ok else "down", "detail": err if not ok else "localhost:9099"}

    return results


async def _get_async_components() -> dict:
    results = {}

    # Audio service
    audio_url = os.getenv("HOSTED_PARAKEET_API_URL", "http://omi-audio:8790")
    audio_health = audio_url.rstrip("/") + "/health"
    ok, code, err = await _check_http(audio_health, timeout=3.0)
    results["audio_service"] = {
        "status": "up" if ok else "down",
        "detail": f"{audio_url} ({code})" if ok else err,
    }

    # LLM backend (LiteLLM or direct llama.cpp — whatever OPENAI_BASE_URL points at)
    llm_base = os.getenv("OPENAI_BASE_URL", "")
    if llm_base:
        llm_health = llm_base.rstrip("/") + "/models"
        ok, _, err = await _check_http(llm_health, timeout=3.0)
        results["llm_backend"] = {
            "status": "up" if ok else "down",
            "detail": llm_base if ok else err,
        }
    else:
        results["llm_backend"] = {"status": "not_configured", "detail": "OPENAI_BASE_URL not set"}

    # ChromaDB vector store
    vector_db = os.getenv("LOCAL_VECTOR_DB", "")
    if vector_db == "chroma":
        from database.chroma_vector import chroma_count, is_chroma_enabled
        if is_chroma_enabled():
            count = chroma_count()
            results["vector_db"] = {"status": "up", "detail": f"ChromaDB ({count} vectors)"}
        else:
            results["vector_db"] = {"status": "down", "detail": "ChromaDB init failed"}
    else:
        pinecone_key = os.getenv("PINECONE_API_KEY", "")
        results["vector_db"] = {
            "status": "up" if pinecone_key else "not_configured",
            "detail": "Pinecone" if pinecone_key else "No LOCAL_VECTOR_DB or PINECONE_API_KEY set",
        }

    # Storage
    storage_backend = os.getenv("STORAGE_BACKEND", "gcs")
    storage_path = os.getenv("LOCAL_STORAGE_PATH", "/data/storage")
    results["storage"] = {
        "status": "up",
        "detail": f"Local FS ({storage_path})" if storage_backend == "local" else f"GCS ({storage_backend})",
    }

    # SearXNG
    searxng_url = os.getenv("SEARXNG_URL", "")
    if searxng_url:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{searxng_url.rstrip('/')}/search?q=test&format=json")
                results["web_search"] = {
                    "status": "up" if resp.status_code == 200 else "down",
                    "detail": f"SearXNG ({searxng_url})",
                }
        except Exception:
            results["web_search"] = {"status": "down", "detail": f"SearXNG unreachable ({searxng_url})"}
    else:
        results["web_search"] = {"status": "not_configured", "detail": "SEARXNG_URL not set (use Perplexity cloud)"}

    return results


# ── HTML dashboard ────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the web dashboard."""
    components = _get_component_status()
    async_components = await _get_async_components()
    components.update(async_components)

    all_up = all(c["status"] == "up" for c in components.values() if c["status"] != "not_configured")
    up_count = sum(1 for c in components.values() if c["status"] == "up")
    total = sum(1 for c in components.values() if c["status"] != "not_configured")

    uptime_sec = int(time.time() - STARTED_AT)
    provider_mode = os.getenv("PROVIDER_MODE", "offline")
    public_url = os.getenv("PUBLIC_URL", f"http://localhost:8000")
    env_stage = os.getenv("OMI_ENV_STAGE", "unknown")

    # Resolve current models (Redis → env)
    from utils.llm.model_config import (
        _get_local_primary_model,
        _get_local_chat_agent_model,
        _get_local_embedding_model,
    )
    primary_model = _get_local_primary_model() or "-"
    chat_agent_model = _get_local_chat_agent_model() or "(pass-through)"
    embedding_model = _get_local_embedding_model() or "-"

    component_rows = ""
    for name, info in sorted(components.items()):
        status_class = {"up": "status-up", "down": "status-down", "not_configured": "status-muted"}
        cls = status_class.get(info["status"], "status-down")
        emoji = {"up": "✅", "down": "❌", "not_configured": "⚪"}.get(info["status"], "❓")
        component_rows += f"""
        <tr>
          <td>{name.replace('_', ' ').title()}</td>
          <td><span class="{cls}">{emoji} {info['status']}</span></td>
          <td>{info['detail']}</td>
        </tr>"""

    public_base = public_url.rstrip("/")

    # Firestore Emulator UI has no auth of its own — only linked when the
    # firestore-ui-proxy sidecar (HTTP Basic Auth) is actually enabled. The
    # link needs the *viewer's* host (public_base's), not the raw emulator
    # port, which is intentionally unpublished — see entrypoint.sh.
    if os.getenv("FIRESTORE_UI_ENABLED", "").strip() == "1":
        _viewer_host = urlparse(public_base).hostname or "localhost"
        _firestore_ui_port = os.getenv("FIRESTORE_UI_PORT", "8086")
        firestore_ui_link = (
            f'<a href="http://{_viewer_host}:{_firestore_ui_port}" class="link-btn" target="_blank">'
            f"Firestore Emulator UI</a>"
        )
    else:
        firestore_ui_link = (
            '<span class="link-btn" style="opacity:0.5;cursor:default" '
            'title="Set FIRESTORE_UI_ENABLED=1 and FIRESTORE_UI_USERNAME/PASSWORD in .env to enable">'
            "Firestore Emulator UI (disabled)</span>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Omi Self-Hosted Dashboard</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0D0D0D;
      color: #F5F5F5;
      max-width: 800px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 4px; }}
    .subtitle {{ color: #888; font-size: 13px; margin-bottom: 24px; }}
    .card {{
      background: #1C1C1E;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
      border: 1px solid #2A2A2E;
    }}
    .card h2 {{ font-size: 15px; font-weight: 600; color: #AAA; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ padding: 10px 8px; border-bottom: 1px solid #2A2A2E; font-size: 14px; }}
    td:first-child {{ font-weight: 500; width: 180px; }}
    .status-up {{ color: #30D158; }}
    .status-down {{ color: #FF453A; }}
    .status-muted {{ color: #666; }}
    .stats {{ display: flex; gap: 24px; flex-wrap: wrap; }}
    .stat {{ flex: 1; min-width: 140px; }}
    .stat-value {{ font-size: 28px; font-weight: 700; color: #30D158; }}
    .stat-label {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
    .stat-value.warn {{ color: #FFD60A; }}
    .stat-value.bad {{ color: #FF453A; }}
    .links {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .link-btn {{
      background: #2A2A2E;
      color: #F5F5F5;
      padding: 8px 16px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 13px;
      border: 1px solid #3A3A3E;
      transition: background 0.2s;
    }}
    .link-btn:hover {{ background: #3A3A3E; }}
    .env-table td:first-child {{ width: 200px; font-family: 'SF Mono', 'Ubuntu Mono', monospace; font-size: 12px; color: #AAA; }}
    .env-table td:last-child {{ font-family: 'SF Mono', 'Ubuntu Mono', monospace; font-size: 12px; color: #F5F5F5; word-break: break-all; }}
    /* model picker */
    .model-select {{ width: 100%; background: #2A2A2E; color: #F5F5F5; border: 1px solid #3A3A3E; border-radius: 8px; padding: 8px 12px; font-size: 14px; margin-top: 4px; }}
    .model-select:focus {{ outline: none; border-color: #64D2FF; }}
    .model-row {{ margin-bottom: 12px; }}
    .model-row label {{ font-size: 13px; color: #AAA; display: block; margin-bottom: 4px; }}
    .save-btn {{
      background: #64D2FF;
      color: #0D0D0D;
      border: none;
      padding: 10px 24px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 8px;
    }}
    .save-btn:hover {{ background: #7DDAFF; }}
    .save-feedback {{ font-size: 13px; margin-left: 12px; color: #30D158; display: none; }}
  </style>
</head>
<body>
  <h1>🜁 Omi Self-Hosted</h1>
  <p class="subtitle">Dashboard — {provider_mode} mode — uptime {uptime_sec//3600}h {(uptime_sec%3600)//60}m</p>

  <div class="stats">
    <div class="stat">
      <div class="stat-value{' bad' if not all_up else ''}">{up_count}/{total}</div>
      <div class="stat-label">Services Healthy</div>
    </div>
    <div class="stat">
      <div class="stat-value">{uptime_sec//3600}h {(uptime_sec%3600)//60}m</div>
      <div class="stat-label">Uptime</div>
    </div>
    <div class="stat">
      <div class="stat-value">{provider_mode}</div>
      <div class="stat-label">Provider Mode</div>
    </div>
  </div>

  <div class="card" style="margin-top: 16px;">
    <h2>🔗 Quick Links</h2>
    <div class="links">
      <a href="/health" class="link-btn" target="_blank">Health Endpoint</a>
      <a href="/docs" class="link-btn" target="_blank">API Docs (Swagger)</a>
      <a href="{public_base}/v1/mcp/sse" class="link-btn" target="_blank">MCP Server</a>
      {firestore_ui_link}
      <a href="/dashboard/models/available" class="link-btn" target="_blank">Model List (JSON)</a>
    </div>
  </div>

  <div class="card">
    <h2>📡 Component Status</h2>
    <table>
      {component_rows}
    </table>
  </div>

  <div class="card">
    <h2>🧠 Model Picker</h2>
    <p style="font-size: 13px; color: #AAA; line-height: 1.6; margin-bottom: 12px;">
      Changes take effect <strong>instantly</strong> — no restart needed.
    </p>
    <div class="model-row">
      <label>Primary model <span style="color: #666;">(memories, summaries, notifications, goals, etc.)</span></label>
      <select id="primary-select" class="model-select">
        <option value="">Loading models...</option>
      </select>
    </div>
    <div class="model-row">
      <label>Chat agent <span style="color: #666;">(conversations, tool use)</span></label>
      <select id="chat-agent-select" class="model-select">
        <option value="">Loading models...</option>
      </select>
    </div>
    <div class="model-row">
      <label>Embedding model <span style="color: #666;">(memory/vector search)</span></label>
      <select id="embedding-select" class="model-select">
        <option value="">Loading models...</option>
      </select>
    </div>
    <button class="save-btn" onclick="saveModels()">Save & Apply</button>
    <span id="save-feedback" class="save-feedback">✓ Saved!</span>
  </div>

  <div class="card">
    <h2>📋 Model Allowlist</h2>
    <p style="font-size: 13px; color: #AAA; line-height: 1.6; margin-bottom: 12px;">
      Only models matching these patterns appear in the picker above and in the mobile app.
      Use <code>*</code> for prefix matching: <code>gpt-4*</code> matches <code>gpt-4o</code>, <code>gpt-4.1</code>, etc.
      Leave blank to show all discovered models from every backend.
    </p>
    <textarea id="allowlist-input" style="width: 100%; background: #2A2A2E; color: #F5F5F5; border: 1px solid #3A3A3E; border-radius: 8px; padding: 10px 12px; font-size: 13px; font-family: 'SF Mono', 'Ubuntu Mono', monospace; min-height: 60px; resize: vertical;" placeholder="e.g. gemma-4-12b,qwen3-8b,gpt-4*,claude-sonnet*,gemini*">Loading...</textarea>
    <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px;">
      <button class="save-btn" onclick="saveAllowlist()">Save Allowlist</button>
      <button class="save-btn" style="background: #3A3A3E; color: #F5F5F5;" onclick="discoverAllModels()">🔍 Show All Available</button>
      <span id="allow-feedback" class="save-feedback">✓ Saved!</span>
    </div>
    <div id="all-models-popup" style="display: none; margin-top: 12px; padding: 12px; background: #1A1A1C; border-radius: 8px; border: 1px solid #3A3A3E; max-height: 300px; overflow-y: auto; font-size: 12px; font-family: 'SF Mono', 'Ubuntu Mono', monospace; color: #AAA;">
    </div>
  </div>

  <div class="card">
    <h2>⚙️ Key Environment</h2>
    <table class="env-table">
      <tr><td>OMI_ENV_STAGE</td><td>{env_stage}</td></tr>
      <tr><td>PROVIDER_MODE</td><td>{provider_mode}</td></tr>
      <tr><td>PUBLIC_URL</td><td>{public_url}</td></tr>
      <tr><td>OPENAI_BASE_URL</td><td>{os.getenv('OPENAI_BASE_URL', '-')}</td></tr>
      <tr><td>OMI_LOCAL_MODEL</td><td>{os.getenv('OMI_LOCAL_MODEL', '-')} → <b>{primary_model}</b> (active)</td></tr>
      <tr><td>LOCAL_LLM_MODEL</td><td>{os.getenv('LOCAL_LLM_MODEL', '-')} → <b>{chat_agent_model}</b> (active)</td></tr>
      <tr><td>LOCAL_EMBEDDING_MODEL</td><td>{os.getenv('LOCAL_EMBEDDING_MODEL', '-')} → <b>{embedding_model}</b> (active)</td></tr>
      <tr><td>HOSTED_PARAKEET_API_URL</td><td>{os.getenv('HOSTED_PARAKEET_API_URL', '-')}</td></tr>
      <tr><td>TTS_LOCAL_BASE_URL</td><td>{os.getenv('TTS_LOCAL_BASE_URL', '-')}</td></tr>
      <tr><td>OMI_ASR_MODEL</td><td>{os.getenv('OMI_ASR_MODEL', '-')}</td></tr>
      <tr><td>REDIS_DB_HOST</td><td>{os.getenv('REDIS_DB_HOST', '-')}:{os.getenv('REDIS_DB_PORT', '6380')}</td></tr>
      <tr><td>FIRESTORE_EMULATOR_HOST</td><td>{os.getenv('FIRESTORE_EMULATOR_HOST', '-')}</td></tr>
      <tr><td>FIREBASE_AUTH_PROJECT_ID</td><td>{os.getenv('FIREBASE_AUTH_PROJECT_ID', '-')}</td></tr>
    </table>
  </div>

  <div class="card">
    <h2>🔁 Conversations Needing Retry</h2>
    <p style="font-size: 13px; color: #AAA; line-height: 1.6; margin-bottom: 12px;">
      Conversations stuck on <code>in_progress</code> with a transcript — usually an LLM call
      that timed out or hit a routing error. Nothing is ever deleted on a failed process; retry
      picks the same transcript back up.
    </p>
    <div id="retry-list">Loading…</div>
  </div>

  <div class="card">
    <h2>📱 Phone Setup</h2>
    <p style="font-size: 13px; color: #AAA; line-height: 1.6;">
      1. Install <code>omi-dev-local.apk</code> on your Android phone<br>
      2. Open <strong>Omi Dev</strong> → Sign in with any Google account<br>
      3. Settings → Developer → Server URL → <code>{public_base}/</code><br>
      4. Settings → Transcription → <strong>Omi Parakeet</strong> (routes to local CPU ASR)<br>
      5. To change the server IP without reinstalling: tap the Server URL field in Developer Settings.
    </p>
  </div>

  <script>
    // Populate model dropdowns from merge proxy (grouped by backend)
    async function loadModels() {{
      try {{
        const resp = await fetch("/dashboard/models/available");
        const data = await resp.json();
        let options = "";
        for (const [backend, models] of Object.entries(data.groups || {{}})) {{
          options += `<optgroup label="${{backend}}">`;
          for (const m of models) {{
            options += `<option value="${{m.id}}">${{m.name}}</option>`;
          }}
          options += "</optgroup>";
        }}
        if (!options) options = '<option value="">No models found</option>';
        document.getElementById("primary-select").innerHTML = options;
        document.getElementById("chat-agent-select").innerHTML = options;
        document.getElementById("embedding-select").innerHTML = options;
        // Pre-select current models
        const current = await fetch("/dashboard/models/current").then(r => r.json());
        document.getElementById("primary-select").value = current.primary_model || "";
        document.getElementById("chat-agent-select").value = current.chat_agent_model || "";
        document.getElementById("embedding-select").value = current.embedding_model || "";
      }} catch (e) {{
        document.getElementById("primary-select").innerHTML = '<option value="">Error loading models</option>';
        document.getElementById("chat-agent-select").innerHTML = '<option value="">Error loading models</option>';
        document.getElementById("embedding-select").innerHTML = '<option value="">Error loading models</option>';
      }}
    }}

    async function saveModels() {{
      const primary = document.getElementById("primary-select").value;
      const chatAgent = document.getElementById("chat-agent-select").value;
      const embedding = document.getElementById("embedding-select").value;
      try {{
        const resp = await fetch("/dashboard/models/select", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ primary: primary, chat_agent: chatAgent, embedding: embedding }})
        }});
        const data = await resp.json();
        const fb = document.getElementById("save-feedback");
        fb.textContent = data.ok ? "✓ Saved — next request uses new models" : "✗ " + (data.error || "save failed");
        fb.style.color = data.ok ? "#30D158" : "#FF453A";
        fb.style.display = "inline";
        setTimeout(() => fb.style.display = "none", 4000);
      }} catch (e) {{
        const fb = document.getElementById("save-feedback");
        fb.textContent = "✗ Request failed";
        fb.style.color = "#FF453A";
        fb.style.display = "inline";
      }}
    }}

    // ── Allowlist management ─────────────────────────────────────────

    async function loadAllowlist() {{
      try {{
        const resp = await fetch("/dashboard/models/allow");
        const data = await resp.json();
        document.getElementById("allowlist-input").value = data.allowlist || "";
      }} catch (e) {{
        document.getElementById("allowlist-input").value = "";
      }}
    }}

    async function saveAllowlist() {{
      const allowlist = document.getElementById("allowlist-input").value.trim();
      try {{
        const resp = await fetch("/dashboard/models/allow", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ allowlist: allowlist }})
        }});
        const data = await resp.json();
        const fb = document.getElementById("allow-feedback");
        fb.textContent = data.ok ? "✓ Saved — picker updates in ~60s" : "✗ " + (data.error || "save failed");
        fb.style.color = data.ok ? "#30D158" : "#FF453A";
        fb.style.display = "inline";
        setTimeout(() => fb.style.display = "none", 4000);
        // Reload the picker to reflect changes
        if (data.ok) loadModels();
      }} catch (e) {{
        const fb = document.getElementById("allow-feedback");
        fb.textContent = "✗ Request failed";
        fb.style.color = "#FF453A";
        fb.style.display = "inline";
      }}
    }}

    async function discoverAllModels() {{
      const popup = document.getElementById("all-models-popup");
      popup.style.display = "block";
      popup.innerHTML = "Loading all models from every backend...";
      try {{
        // Fetch models without the allowlist filter by hitting the merge proxy directly
        // with a cache-bust. The proxy returns filtered results, so we show the current list
        // grouped by backend with click-to-add.
        const resp = await fetch("/dashboard/models/available");
        const data = await resp.json();
        let html = "";
        for (const [backend, models] of Object.entries(data.groups || {{}})) {{
          html += `<div style="color: #64D2FF; margin-top: 8px; font-weight: 600;">${{backend}}</div>`;
          for (const m of models) {{
            html += `<div style="padding: 2px 0; cursor: pointer;" onclick="addToAllowlist('${{m.name}}')" title="Click to add">+ ${{m.name}}</div>`;
          }}
        }}
        if (!html) html = "No models available. Set MODEL_ALLOW in env or save an allowlist above.";
        popup.innerHTML = html;
      }} catch (e) {{
        popup.innerHTML = "Error fetching models: " + e.message;
      }}
    }}

    function addToAllowlist(name) {{
      const input = document.getElementById("allowlist-input");
      const current = input.value.trim();
      input.value = current ? current + "," + name : name;
      document.getElementById("all-models-popup").style.display = "none";
    }}

    async function loadRetryList() {{
      const el = document.getElementById("retry-list");
      try {{
        const resp = await fetch("/dashboard/conversations");
        const data = await resp.json();
        if (!data.conversations || data.conversations.length === 0) {{
          el.innerHTML = '<p style="font-size: 13px; color: #666;">Nothing stuck — all caught up.</p>';
          return;
        }}
        el.innerHTML = data.conversations.map(c => `
          <div style="padding: 12px 0; border-bottom: 1px solid #2A2A2E; display: flex; justify-content: space-between; align-items: center; gap: 12px;">
            <div style="flex: 1; min-width: 0;">
              <div style="font-size: 13px; color: #F5F5F5; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${{c.title || c.preview || '(no transcript preview)'}}</div>
              <div style="font-size: 11px; color: #666; margin-top: 2px;">${{c.segment_count}} segments · ${{c.created_at}}</div>
            </div>
            <button class="save-btn" style="margin-top: 0; white-space: nowrap;" onclick="retryConversation('${{c.id}}', '${{c.uid}}', this)">Retry</button>
          </div>
        `).join("");
      }} catch (e) {{
        el.innerHTML = '<p style="font-size: 13px; color: #FF453A;">Error loading: ' + e.message + '</p>';
      }}
    }}

    async function retryConversation(id, uid, btn) {{
      btn.disabled = true;
      btn.textContent = "Retrying…";
      try {{
        const resp = await fetch(`/dashboard/conversations/${{id}}/retry?uid=${{encodeURIComponent(uid)}}`, {{ method: "POST" }});
        const data = await resp.json();
        if (data.ok) {{
          btn.textContent = "✓ Done";
          setTimeout(loadRetryList, 1000);
        }} else {{
          btn.textContent = "✗ Failed";
          btn.title = data.error || "unknown error";
          btn.disabled = false;
        }}
      }} catch (e) {{
        btn.textContent = "✗ Failed";
        btn.disabled = false;
      }}
    }}

    loadModels();
    loadAllowlist();
    loadRetryList();
  </script>

  <p style="text-align: center; color: #444; font-size: 11px; margin-top: 32px;">
    Omi self-hosted &bull; LiteLLM multi-model &bull; Dashboard v2 &bull; {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── JSON endpoints ─────────────────────────────────────────────────────


@router.get("/dashboard/components", response_class=JSONResponse)
async def components_status():
    components = _get_component_status()
    async_components = await _get_async_components()
    components.update(async_components)
    return {
        "uptime_seconds": int(time.time() - STARTED_AT),
        "provider_mode": os.getenv("PROVIDER_MODE", "offline"),
        "components": components,
    }


@router.get("/dashboard/models/available", response_class=JSONResponse)
async def models_available():
    """Return all models the merge proxy can route to, grouped by backend.

    Queries OPENAI_BASE_URL's /v1/models. Each model has a tag prefix
    (e.g. 'llama/gemma-4-12b', 'or/openai/gpt-4o'). The response groups
    them by backend for clean dropdown rendering.
    """
    discovered_models: list[dict] = []
    openai_base = os.getenv("OPENAI_BASE_URL", "")

    if openai_base:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{openai_base.rstrip('/')}/models")
                if resp.status_code == 200:
                    data = resp.json()
                    discovered_models = [
                        {"id": m.get("id", "unknown"), "owned_by": m.get("owned_by", "")}
                        for m in data.get("data", [])
                    ]
        except Exception:
            pass

    # Group by backend tag
    groups: dict[str, list[dict]] = {}
    for m in discovered_models:
        tag = m["owned_by"] or "other"
        groups.setdefault(tag, []).append(m)

    return {
        "models": discovered_models,
        "groups": {
            tag: [{"id": m["id"], "name": _model_display_name(m["id"], tag)}
                  for m in models]
            for tag, models in sorted(groups.items())
        },
        "count": len(discovered_models),
        "backends": sorted(groups.keys()),
        "source": openai_base or "not configured",
    }


def _model_display_name(full_id: str, tag: str) -> str:
    """Strip the tag prefix for display: 'llama/gemma-4-12b' → 'gemma-4-12b'."""
    prefix = tag + "/"
    if full_id.startswith(prefix):
        return full_id[len(prefix):]
    return full_id


# ── allowlist management ────────────────────────────────────────────────


@router.get("/dashboard/models/allow", response_class=JSONResponse)
async def models_allow_get():
    """Return the current model allowlist (from Redis, or env, or empty)."""
    allowlist = ""
    source = "none"
    try:
        from database.redis_db import get_model_allowlist
        allowlist = get_model_allowlist() or ""
        source = "redis" if allowlist else "env"
    except Exception:
        pass
    if not allowlist:
        allowlist = os.getenv("MODEL_ALLOW", "")
        if allowlist:
            source = "env"
    return {
        "allowlist": allowlist,
        "source": source,
        "note": "Comma-separated patterns. Empty = show all. Use * for prefix: gpt-4*",
    }


@router.post("/dashboard/models/allow", response_class=JSONResponse)
async def models_allow_set(req: ModelAllowRequest):
    """Save a model allowlist. Writes to Redis (shared with merge proxy).

    After saving, notifies the merge proxy to flush its cache so the
    next /v1/models request re-filters with the new allowlist applied.
    Dashboard picker and mobile APK will show only allowed models.
    """
    try:
        from database.redis_db import set_model_allowlist, get_model_allowlist
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"Redis unavailable: {e}"},
        )

    set_model_allowlist(req.allowlist)

    # Notify the merge proxy to flush its model cache
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post("http://omi-proxy:4000/allowlist/reload")
    except Exception:
        pass  # best-effort — proxy picks up the new allowlist on next cache expiry

    return {
        "ok": True,
        "allowlist": req.allowlist,
        "note": "Allowlist saved. The merge proxy will pick it up on next model refresh. "
                "Dashboard picker and mobile APK will show only matching models.",
    }


@router.get("/dashboard/models/current", response_class=JSONResponse)
async def models_current():
    """Return the currently active models (Redis → env fallback)."""
    from utils.llm.model_config import (
        _get_local_primary_model,
        _get_local_chat_agent_model,
        _get_local_embedding_model,
    )

    primary = _get_local_primary_model() or ""
    chat_agent = _get_local_chat_agent_model() or ""
    embedding = _get_local_embedding_model() or ""
    openai_base = os.getenv("OPENAI_BASE_URL", "")

    # Check if overrides are from Redis or env
    primary_source = "env"
    chat_source = "env"
    embedding_source = "env"
    try:
        from database.redis_db import get_runtime_model
        if get_runtime_model("primary"):
            primary_source = "redis"
        if get_runtime_model("chat_agent"):
            chat_source = "redis"
        if get_runtime_model("embedding"):
            embedding_source = "redis"
    except Exception:
        pass

    return {
        "local_mode": bool(openai_base and primary),
        "primary_model": primary or "none (cloud)",
        "chat_agent_model": chat_agent or "(pass-through)",
        "embedding_model": embedding or "(default)",
        "openai_base_url": openai_base or "not set",
        "provider_mode": os.getenv("PROVIDER_MODE", "offline"),
        "primary_source": primary_source,
        "chat_agent_source": chat_source,
        "embedding_source": embedding_source,
    }


@router.post("/dashboard/models/select", response_class=JSONResponse)
async def models_select(req: ModelSelectRequest):
    """Set runtime model overrides. Changes take effect on the next LLM call.

    The next request through get_llm() picks up the new models —
    no restart needed.
    """
    try:
        from database.redis_db import set_runtime_model
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"Redis unavailable: {e}"},
        )

    if req.primary:
        set_runtime_model("primary", req.primary)
    if req.chat_agent:
        set_runtime_model("chat_agent", req.chat_agent)
    if req.embedding:
        set_runtime_model("embedding", req.embedding)

    # Report back what's now active
    from utils.llm.model_config import (
        _get_local_primary_model,
        _get_local_chat_agent_model,
        _get_local_embedding_model,
    )

    return {
        "ok": True,
        "primary": _get_local_primary_model() or "",
        "chat_agent": _get_local_chat_agent_model() or "",
        "embedding": _get_local_embedding_model() or "",
        "note": "Models applied. Next LLM call will use the new selection.",
    }


@router.get("/dashboard/models", response_class=JSONResponse)
async def model_config():
    """Legacy endpoint — returns current config. Prefer /models/available + /models/current."""
    from utils.llm.model_config import _get_local_primary_model, _get_local_chat_agent_model

    local_model = _get_local_primary_model()
    openai_base = os.getenv("OPENAI_BASE_URL", "")
    local_llm_model = _get_local_chat_agent_model()
    provider_mode = os.getenv("PROVIDER_MODE", "offline")

    discovered_models = []
    if openai_base:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{openai_base.rstrip('/')}/models")
                if resp.status_code == 200:
                    data = resp.json()
                    discovered_models = [
                        {"id": m.get("id", "unknown"), "owned_by": m.get("owned_by", "")}
                        for m in data.get("data", [])
                    ]
        except Exception:
            pass

    return {
        "local_mode": bool(openai_base and local_model),
        "primary_model": local_model or "none (cloud)",
        "chat_agent_model": local_llm_model or "(pass-through)",
        "openai_base_url": openai_base or "not set",
        "provider_mode": provider_mode,
        "discovered_models": discovered_models,
        "note": "Use /dashboard/models/available for the full list, /dashboard/models/select to switch, /dashboard/models/current for active state.",
    }


# ── conversation history / retry ────────────────────────────────────────
#
# The dashboard has no per-request session, so these list/retry across every
# local account via a collection_group query rather than a single uid — fine
# for this single-operator self-hosted deployment, not a multi-tenant admin
# panel. A conversation stuck on "in_progress" with a transcript but no
# structured summary is exactly the shape a failed LLM call (timeout, routing
# 404) leaves behind — see utils/conversations/lifecycle.py's
# rollback_processing_admission, which reverts to in_progress instead of
# deleting on failure. This surface just makes that already-safe state visible
# and retryable without a manual curl + reprocess call.


@router.get("/dashboard/conversations", response_class=JSONResponse)
async def dashboard_conversations(limit: int = 20):
    """List recent in-progress conversations (with a transcript) across all local accounts."""
    from database._client import get_firestore_client

    db = get_firestore_client()
    query = (
        db.collection_group('conversations')
        .where('status', '==', 'in_progress')
        .order_by('created_at', direction='DESCENDING')
        .limit(limit)
    )
    items = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        segments = data.get('transcript_segments') or []
        if not segments:
            continue
        uid = doc.reference.parent.parent.id
        preview = " ".join(s.get('text', '') for s in segments[:3]).strip()
        items.append(
            {
                "id": doc.id,
                "uid": uid,
                "created_at": str(data.get('created_at', '')),
                "segment_count": len(segments),
                "preview": (preview[:140] + "…") if len(preview) > 140 else preview,
                "title": (data.get('structured') or {}).get('title', ''),
            }
        )
    return {"conversations": items, "count": len(items)}


@router.post("/dashboard/conversations/{conversation_id}/retry", response_class=JSONResponse)
async def dashboard_retry_conversation(conversation_id: str, uid: str):
    """Force-reprocess a stuck in-progress conversation for the given uid.

    Calls the same reprocess_conversation logic the app's retry button uses
    (routers/conversations.py) directly, bypassing its auth Depends since the
    dashboard already resolved uid via the collection_group listing above.
    """
    from routers.conversations import reprocess_conversation

    try:
        conversation = reprocess_conversation(conversation_id, uid=uid)
        status = conversation.status
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "status": status.value if hasattr(status, "value") else status,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})