#!/usr/bin/env bash
# Entrypoint for the Omi local host container.
# Starts: redis (6380), firebase firestore+auth emulators (8085/9099), backend (8000).
# Provider mode: "offline" = no external keys (STT/LLM calls are inert);
#                "real" = uses OPENAI_API_KEY / DEEPGRAM_API_KEY from environment.
set -uo pipefail
cd /app

STATE=/data
mkdir -p "$STATE/logs" "$STATE/redis" "$STATE/firebase-export"
export PATH="/app/backend/.venv/bin:$PATH"

echo "=== Omi local host container ==="
echo "provider mode: ${PROVIDER_MODE:-offline}"

# ---- Redis ----
# AOF persistence, pointed at the already-mounted /data volume: the
# dashboard's model picker (and other runtime settings) live only in Redis,
# no Firestore backing. --save "" --appendonly no discarded all of that on
# every restart — same class of bug as the Firebase emulator's lost
# export-on-exit below, just silent since there's no login error to notice
# it by, only a stale/empty model selection routing nowhere.
redis-server --bind 0.0.0.0 --port 6380 --dir "$STATE/redis" --appendonly yes --appendfsync everysec \
  > "$STATE/logs/redis.log" 2>&1 &
REDIS_PID=$!
echo "redis: pid $REDIS_PID"

# ---- Firebase emulators (firestore + auth) ----
# firebase.json binds emulators to 127.0.0.1 — fine bare-metal, but inside this
# container the published :9099/:8085 ports DNAT to the container's eth0, not its
# loopback. Rewrite hosts to 0.0.0.0 into a container-local config so the phone
# can reach the auth emulator through the published port.
#
# Also adds a "ui" block here rather than in the shared repo-root firebase.json
# (which scripts/dev-harness and others also read) — the Emulator UI (Firestore
# data browser) was never actually enabled, so the dashboard's "Firestore
# Emulator UI" link has never worked. It binds to 0.0.0.0:4000 like the other
# emulators, but that port is deliberately NOT published in docker-compose.yml —
# it has no auth of its own, so it's only reachable via the optional
# firestore-ui-proxy sidecar (HTTP Basic Auth, FIRESTORE_UI_ENABLED=1).
python - <<'PY'
import json
cfg = json.load(open('/app/firebase.json'))
for name, emu in cfg.get('emulators', {}).items():
    emu['host'] = '0.0.0.0'
cfg.setdefault('emulators', {})['ui'] = {'enabled': True, 'host': '0.0.0.0', 'port': 4000}
json.dump(cfg, open('/app/firebase.docker.json', 'w'), indent=2)
PY
firebase emulators:start --config /app/firebase.docker.json --only firestore,auth,ui --project demo-omi-local \
  --import "$STATE/firebase-export" --export-on-exit "$STATE/firebase-export" \
  > "$STATE/logs/firebase-emulators.log" 2>&1 &
FIREBASE_PID=$!
echo "firebase emulators: pid $FIREBASE_PID"

# --export-on-exit only fires on the emulator's own graceful shutdown. This
# script used to `exec` straight into uvicorn, which replaces PID 1 and
# permanently severs any chance of forwarding Docker's stop signal to this
# backgrounded emulator — every `docker stop`/`restart` silently lost all
# local accounts. Stay PID 1 and forward the signal instead, sequentially:
# wait for the export to fully finish before touching anything else, so a
# slow flush can't race a simultaneous kill of the backend/redis. SIGINT
# (not TERM) for firebase specifically — the Node CLI is built and tested
# against Ctrl-C far more than SIGTERM, and empirically this is what
# actually triggers the export reliably.
shutdown() {
  echo "shutting down: signaling firebase emulators (pid $FIREBASE_PID) to export..."
  kill -INT "$FIREBASE_PID" 2>/dev/null
  wait "$FIREBASE_PID" 2>/dev/null
  echo "firebase emulators exited, export complete"
  [ -n "${BACKEND_PID:-}" ] && kill -TERM "$BACKEND_PID" 2>/dev/null
  kill -TERM "$REDIS_PID" 2>/dev/null
  [ -n "${LLM_ADAPTER_PID:-}" ] && kill -TERM "$LLM_ADAPTER_PID" 2>/dev/null
  exit 0
}
trap shutdown TERM INT

# wait for auth emulator
for i in $(seq 1 90); do
  if (exec 3<>/dev/tcp/127.0.0.1/9099) 2>/dev/null; then exec 3>&-; break; fi
  sleep 1
done
echo "auth emulator ready"

# ---- Backend ----
cd /app/backend
# Stage mapping mirrors scripts/dev-harness (config.py): offline -> "offline",
# anything else -> "local" (emulator harness). "real" is NOT a valid stage.
if [ "${PROVIDER_MODE:-offline}" = "offline" ]; then
  export OMI_ENV_STAGE=offline
else
  export OMI_ENV_STAGE=local
fi
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8085
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
export FIREBASE_AUTH_PROJECT_ID=demo-omi-local
export FIREBASE_PROJECT_ID=demo-omi-local
export FIRESTORE_DATABASE_ID=default
export FIREBASE_API_KEY="${FIREBASE_API_KEY:-local-firebase-auth-emulator-api-key}"
export MEMORY_MODE=read
export MEMORY_CANONICAL_CONSOLIDATION_ENABLED=true
export REDIS_DB_HOST=127.0.0.1 REDIS_DB_PORT=6380 REDIS_DB_PASSWORD=
export ENVIRONMENT=local-dev-harness
export ENCRYPTION_SECRET="${ENCRYPTION_SECRET:-omi_local_dev_harness_32_byte_test_secret_not_prod}"
export ADMIN_KEY="${ADMIN_KEY:-local-dev-admin-key-}"
# Typesense intentionally unconfigured (conversation full-text search is optional;
# the client falls back to Firestore queries). Leave unset.
export BASE_API_URL="${BASE_API_URL:-http://127.0.0.1:8000}"
export API_BASE_URL="${BASE_API_URL}"
export OMI_LLM_GATEWAY_FEATURE_MODE=off
export PORT=8000 PYTHONUNBUFFERED=1

# Optional: route chat/LLM calls to a local OpenAI-compatible server
# (e.g. llama.cpp --port 8081 on the MI50s). Model names (gpt-5.6-luna, gpt-5-nano)
# can be aliased with llama.cpp's --alias / -cv options or an OpenAI-compatible proxy.
if [ -n "${OPENAI_BASE_URL:-}" ]; then
  export OPENAI_BASE_URL
  echo "LLM routed to OPENAI_BASE_URL=$OPENAI_BASE_URL"
  # Anthropic->OpenAI adapter: the main agentic chat uses the Anthropic SDK
  # (claude-sonnet-4-6). The adapter translates the Messages API (streaming +
  # tool calls) to the OpenAI-compatible server, so that path is local too.
  # The backend's anthropic SDK picks up ANTHROPIC_BASE_URL from the env.
  python /app/omi-host/local_llm_adapter.py > "$STATE/logs/llm-adapter.log" 2>&1 &
  LLM_ADAPTER_PID=$!
  echo "llm-adapter: pid $LLM_ADAPTER_PID (Anthropic API on :${LLM_ADAPTER_PORT:-8788})"
  export ANTHROPIC_BASE_URL="http://127.0.0.1:${LLM_ADAPTER_PORT:-8788}"
  export ANTHROPIC_API_KEY="omi-local-llm-adapter-no-auth"
fi

# Public URL for the dashboard
export PUBLIC_URL="${PUBLIC_URL:-http://0.0.0.0:8000}"

if [ "${PROVIDER_MODE:-offline}" = "offline" ]; then
  export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-omi-local-harness-offline-openai-not-real}"
  export GEMINI_API_KEY="${GEMINI_API_KEY:-omi-local-harness-offline-gemini-not-real}"
  export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-omi-local-harness-offline-anthropic-not-real}"
  # Fully-local audio (default): point STT/diarization/TTS at the omi-audio
  # sibling container. DEEPGRAM_API_KEY and MODULATE_API_KEY are intentionally
  # LEFT UNSET so the provider policy never selects a cloud engine — selection
  # falls through to the local Parakeet protocol served by omi-audio. Set
  # DEEPGRAM_API_KEY / MODULATE_API_KEY to fall back to cloud if you want.
  export HOSTED_PARAKEET_API_URL="${HOSTED_PARAKEET_API_URL:-http://omi-audio:8790}"
  export HOSTED_SPEAKER_EMBEDDING_API_URL="${HOSTED_SPEAKER_EMBEDDING_API_URL:-http://omi-audio:8790}"
  export TTS_LOCAL_BASE_URL="${TTS_LOCAL_BASE_URL:-http://omi-audio:8790}"
  echo "Audio (STT/diarization/TTS) -> local CPU service at ${HOSTED_PARAKEET_API_URL}"
  echo "NOTE: offline mode — no cloud AI keys. LLM via OPENAI_BASE_URL (llama.cpp)."
fi

echo "backend: starting"
uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
wait "$BACKEND_PID"
