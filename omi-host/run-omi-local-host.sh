#!/usr/bin/env bash
# run-omi-local-host.sh — run on the UNRAID HOST (ssh root@192.168.20.5), NOT in a container.
#
# Starts THREE containers (one compose project, shared omi-data volume):
#   omi-proxy  — auto-discovering model merge proxy (:4000)
#   omi-local  — backend (:8000) + Firebase Auth (:9099) + Firestore emulator + Redis
#   omi-audio  — fully-offline CPU audio: faster-whisper STT + diarization + Piper TTS
#
# Usage:
#   1. Copy the package:  scp omi-host.tar.gz root@host:~
#   2. bash ~/omi-host/run-omi-local-host.sh
#
# Options via environment:
#   BIND_IP=192.168.20.5              (auto-detected from default route if unset)
#   PROVIDER_MODE=offline|real        (default offline)
#   LLAMA_HOST=172.19.0.4             (llama.cpp host — for merge proxy auto-discovery)
#   LLAMA_PORT=8081                   (llama.cpp port)
#   OPENROUTER_API_KEY=sk-or-...      (auto-adds OpenRouter to merge proxy if set)
#   OMI_LOCAL_MODEL=llama/gemma-4-12b (default primary model — merge proxy name)
#   LOCAL_LLM_MODEL=llama/gemma-4-12b (default chat agent model)
#   OPENAI_API_KEY=local              (key for llama.cpp / local backends)
#   OMI_ASR_MODEL=Systran/faster-whisper-small  (STT model for omi-audio)
#   DEEPGRAM_API_KEY=***              (optional — re-enables CLOUD STT)
#   MODULATE_API_KEY=***              (optional — re-enables CLOUD STT)
#
# After startup: http://${BIND_IP}:8000/ — dashboard with model picker showing
# all models from ALL backends (llama.cpp + OpenRouter merged automatically).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$HERE/repo"

# Persistent overrides: copy .env.example -> .env and edit it. Anything set
# there behaves exactly like exporting it before running this script (every
# ${VAR:-default} below, and the values handed to `docker compose up`, both
# see it) — no need to retype env vars on the command line every time.
if [ -f "$HERE/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$HERE/.env"
  set +a
fi

if [ -n "${BIND_IP:-}" ]; then
  BIND_IP="${BIND_IP}"
else
  BIND_IP="$(ip route get 1 2>/dev/null | awk '{print $7; exit}' || echo '0.0.0.0')"
fi

echo "=== Omi local host installer ==="
echo "package: $HERE"
echo "bind IP:  $BIND_IP"

if [ ! -f "$REPO/backend/main.py" ]; then
  echo "error: repo/backend/main.py not found next to this script." >&2
  echo "  Create a symlink: ln -s .. omi-host/repo" >&2
  exit 1
fi

# 0. Piper voice
if ! ls "${PIPER_VOICE_DIR:-/mnt/user/appdata/omi-local-audio/piper/voices}"/en_US-lessac-medium.onnx >/dev/null 2>&1; then
  echo "=== Downloading Piper voice (en_US-lessac-medium, ~60 MB) ==="
  bash "$HERE/download-piper-voice.sh" en_US-lessac-medium || echo "voice download failed — TTS will 503 until you run it manually"
fi

# 1. Build images
echo "=== Building images (first run takes a few minutes) ==="
docker build -t omi-local:latest       -f "$REPO/omi-host/Dockerfile"       "$REPO"
docker build -t omi-local-audio:latest -f "$REPO/omi-host/Dockerfile.audio" "$REPO/omi-host"
docker build -t omi-proxy:latest       -f "$HERE/Dockerfile.proxy"          "$HERE"

# 2. Build UPSTREAMS JSON from env — adds llama.cpp and optionally OpenRouter.
#    Default: just llama.cpp at LLAMA_HOST:LLAMA_PORT
_llama_host="${LLAMA_HOST:-172.19.0.4}"
_llama_port="${LLAMA_PORT:-8081}"
_llama_base="http://${_llama_host}:${_llama_port}/v1"
echo "LLM backend (llama.cpp): ${_llama_base}"

if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  echo "OpenRouter: enabled (models auto-discovered)"
else
  echo "OpenRouter: not configured (set OPENROUTER_API_KEY to add)"
fi

# 3. Start the stack
cd "$HERE"
echo "=== Starting stack ==="
# Passing -f explicitly (needed for the base file) disables compose's automatic
# docker-compose.override.yml merge, so re-add it by hand when present.
COMPOSE_FILES=(-f docker-compose.yml)
if [ -f "$HERE/docker-compose.override.yml" ]; then
  echo "Applying docker-compose.override.yml"
  COMPOSE_FILES+=(-f docker-compose.override.yml)
fi
BIND_IP="$BIND_IP" \
PROVIDER_MODE="${PROVIDER_MODE:-offline}" \
LLAMA_HOST="${_llama_host}" \
LLAMA_PORT="${_llama_port}" \
OPENAI_API_KEY="${OPENAI_API_KEY:-local}" \
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" \
LOCAL_LLM_MODEL="${LOCAL_LLM_MODEL:-}" \
OMI_LOCAL_MODEL="${OMI_LOCAL_MODEL:-}" \
DEEPGRAM_API_KEY="${DEEPGRAM_API_KEY:-}" \
MODULATE_API_KEY="${MODULATE_API_KEY:-}" \
OMI_ASR_MODEL="${OMI_ASR_MODEL:-Systran/faster-whisper-small}" \
LOCAL_VECTOR_DB="${LOCAL_VECTOR_DB:-chroma}" \
SEARXNG_URL="${SEARXNG_URL:-}" \
STORAGE_BACKEND="${STORAGE_BACKEND:-local}" \
LOCAL_STORAGE_PATH="${LOCAL_STORAGE_PATH:-/data/storage}" \
CHROMA_DATA_PATH="${CHROMA_DATA_PATH:-/data/chroma/vector_db}" \
docker compose "${COMPOSE_FILES[@]}" up -d

if [ "${SEARXNG_ENABLED:-0}" = "1" ]; then
  echo "=== Starting SearXNG (local web search) ==="
  docker compose "${COMPOSE_FILES[@]}" --profile searxng up -d searxng
fi

if [ "${FIRESTORE_UI_ENABLED:-0}" = "1" ]; then
  echo "=== Starting Firestore Emulator UI proxy (HTTP Basic Auth) ==="
  docker compose "${COMPOSE_FILES[@]}" --profile firestore-ui up -d firestore-ui-proxy
fi

if [ "${OMI_APPS_ENABLED:-0}" = "1" ]; then
  echo "=== Starting chat-tool apps (Wikipedia/Open Library/Open-Meteo/Open Food Facts gateway, Hermes Agent bridge) ==="
  HERMES_API_URL="${HERMES_API_URL:-}" \
  HERMES_API_KEY="${HERMES_API_KEY:-}" \
  OMI_ALLOWED_UIDS="${OMI_ALLOWED_UIDS:-}" \
  OMI_ALLOWED_APP_IDS="${OMI_ALLOWED_APP_IDS:-}" \
  docker compose "${COMPOSE_FILES[@]}" --profile omi-apps up -d \
    omi-apps-gateway omi-app-hermes-agent
fi

# 4. Wait for health
echo "=== Waiting for merge proxy ==="
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:4000/health" >/dev/null 2>&1; then
    echo "proxy HEALTHY after ${i}s"
    break
  fi
  sleep 1
done

echo "=== Waiting for backend health ==="
for i in $(seq 1 180); do
  if curl -sf "http://${BIND_IP}:8000/health" >/dev/null 2>&1; then
    echo "backend HEALTHY after ${i}s"
    break
  fi
  sleep 1
done
curl -sf "http://${BIND_IP}:8000/health" >/dev/null 2>&1 || {
  echo "backend not healthy — check: docker logs omi-local" >&2
  exit 1
}

echo "=== Waiting for audio service (model load, ~1-2 min first run) ==="
for i in $(seq 1 240); do
  if curl -sf http://127.0.0.1:8790/health >/dev/null 2>&1; then
    echo "audio HEALTHY after ${i}s"
    break
  fi
  sleep 1
done
curl -sf http://127.0.0.1:8790/health >/dev/null 2>&1 || {
  echo "audio service not healthy — check: docker logs omi-audio" >&2
  exit 1
}

echo
echo "=== DONE ==="
echo "Dashboard:     http://${BIND_IP}:8000/  (model picker shows ALL backends)"
echo "Model list:    http://${BIND_IP}:8000/dashboard/models/available"
echo "Auth emulator: http://${BIND_IP}:9099/"
echo "Merge proxy:   http://127.0.0.1:4000/health"
echo "Local audio:   http://127.0.0.1:8790/health (CPU STT + diarization + TTS)"
echo "Logs:          docker logs -f omi-local | docker logs -f omi-proxy | docker logs -f omi-audio"
echo "Stop:          cd $HERE && docker compose down"
echo
echo "Startup example with OpenRouter:"
echo "  LLAMA_HOST=172.19.0.4 OPENROUTER_API_KEY=sk-or-... bash run-omi-local-host.sh"
echo
echo "Phone setup:"
echo "  1. Seed your login account (once):"
echo "     docker exec -it omi-local python backend/scripts/seed_local_account.py --username you"
echo "  2. Install the local-only APK build → Log in with:"
echo "     Server IP: ${BIND_IP}   Username/password: whatever you just seeded"
echo "  Settings > Transcription → 'Omi Parakeet' (server) or 'On-device Whisper' (phone)"
echo "  Speaker diarization & voice training work with both — server handles embeddings"