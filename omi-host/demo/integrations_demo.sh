#!/usr/bin/env bash
# Connections demo: proves what Omi sends to the family wiki, Odysseus, Hermes, Studio,
# Coinpile and Pondering Orb, and that nothing leaves without the user's OK.
#
# Starts its own stack from this checkout, like caldav_demo.sh does (redis, the Firestore +
# Auth emulators and the backend with the container's environment, the in-process scheduler
# on, local login), on its own ports, plus:
#   * the REAL LLMWIKI MCP bridge handler (wiki_bridge_server.cjs, node, over the wiki repo's
#     own in-memory test vault), and
#   * recording stand-ins for Hermes, Odysseus, Studio, Coinpile and Orb (agents_stub.py),
# all listening on this machine's LAN address (Omi refuses loopback targets; the address is
# allowed through OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS, exactly as an operator would). The
# backend runs under omi-host's egress guard (block mode), as in the container.
# Then runs omi-host/demo/integrations_demo.py and tears everything down.
#
#   omi-host/demo/integrations_demo.sh            run, print the PASS/FAIL table, tear down
#   omi-host/demo/integrations_demo.sh --keep     leave the stack running afterwards
#
# Ports (defaults differ from caldav_demo.sh so both can run):
#   OMI_DEMO_BACKEND_PORT=8100 OMI_DEMO_FIRESTORE_PORT=8185 OMI_DEMO_AUTH_PORT=9199
#   OMI_DEMO_REDIS_PORT=6490 OMI_DEMO_HUB_PORT=4510 OMI_DEMO_WIKI_PORT=27223
#   OMI_DEMO_HERMES_PORT=8742 OMI_DEMO_ODYSSEUS_PORT=7100 OMI_DEMO_STUDIO_PORT=8748
#   OMI_DEMO_COINPILE_PORT=8180 OMI_DEMO_ORB_PORT=3100
# OMI_DEMO_LAN_IP: the address the stand-ins listen on (default: this machine's first
# non-loopback IPv4). LLMWIKI_REPO: the LLM-will-remember-this checkout (default
# ../wizard815/LLM-will-remember-this next to this repo, else /home/user/wizard815/...).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
KEEP=0
DRIVER_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    *) DRIVER_ARGS+=("$arg") ;;
  esac
done

BACKEND_PORT="${OMI_DEMO_BACKEND_PORT:-8100}"
FIRESTORE_PORT="${OMI_DEMO_FIRESTORE_PORT:-8185}"
AUTH_PORT="${OMI_DEMO_AUTH_PORT:-9199}"
REDIS_PORT="${OMI_DEMO_REDIS_PORT:-6490}"
HUB_PORT="${OMI_DEMO_HUB_PORT:-4510}"
WIKI_PORT="${OMI_DEMO_WIKI_PORT:-27223}"
HERMES_PORT="${OMI_DEMO_HERMES_PORT:-8742}"
ODYSSEUS_PORT="${OMI_DEMO_ODYSSEUS_PORT:-7100}"
STUDIO_PORT="${OMI_DEMO_STUDIO_PORT:-8748}"
COINPILE_PORT="${OMI_DEMO_COINPILE_PORT:-8180}"
ORB_PORT="${OMI_DEMO_ORB_PORT:-3100}"
PY="${OMI_DEMO_PYTHON:-$REPO/backend/.venv/bin/python}"
FIREBASE_BIN="${FIREBASE_BIN:-$REPO/node_modules/.bin/firebase}"
NODE_BIN="${NODE_BIN:-node}"
LAN_IP="${OMI_DEMO_LAN_IP:-$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | grep -m1 '\.' || true)}"
if [ -z "${LLMWIKI_REPO:-}" ]; then
  for candidate in "$REPO/../wizard815/LLM-will-remember-this" /home/user/wizard815/LLM-will-remember-this; do
    [ -f "$candidate/scripts/family-harness.cjs" ] && LLMWIKI_REPO="$(cd "$candidate" && pwd)" && break
  done
fi
[ -n "$LAN_IP" ] || { echo "error: no LAN address found; set OMI_DEMO_LAN_IP" >&2; exit 2; }
[ -f "${LLMWIKI_REPO:-}/scripts/family-harness.cjs" ] || { echo "error: set LLMWIKI_REPO to an LLM-will-remember-this checkout" >&2; exit 2; }

STATE="${OMI_DEMO_STATE:-$(mktemp -d -t omi-integrations-demo.XXXXXX)}"
mkdir -p "$STATE/logs" "$STATE/redis" "$STATE/emulators"
echo "demo state: $STATE; stand-ins on $LAN_IP; wiki repo $LLMWIKI_REPO"

port_busy() { (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null; }
for spec in "backend:127.0.0.1:$BACKEND_PORT" "firestore:127.0.0.1:$FIRESTORE_PORT" "auth:127.0.0.1:$AUTH_PORT" \
            "redis:127.0.0.1:$REDIS_PORT" "hub:127.0.0.1:$HUB_PORT" "wiki:$LAN_IP:$WIKI_PORT" \
            "hermes:$LAN_IP:$HERMES_PORT" "odysseus:$LAN_IP:$ODYSSEUS_PORT" "studio:$LAN_IP:$STUDIO_PORT" \
            "coinpile:$LAN_IP:$COINPILE_PORT" "orb:$LAN_IP:$ORB_PORT"; do
  name="${spec%%:*}"; rest="${spec#*:}"
  if port_busy "${rest%:*}" "${rest##*:}"; then
    echo "error: port ${rest##*:} ($name) is in use; set OMI_DEMO_${name^^}_PORT (see the header)" >&2
    exit 2
  fi
done
for bin in "$PY" "$FIREBASE_BIN"; do
  [ -x "$bin" ] || { echo "error: $bin missing (see caldav_demo.sh's header)" >&2; exit 2; }
done
command -v redis-server >/dev/null || { echo "error: redis-server missing" >&2; exit 2; }
command -v "$NODE_BIN" >/dev/null || { echo "error: node missing" >&2; exit 2; }

PIDS=()
teardown() {
  local code=$?
  if [ "$KEEP" = "1" ]; then
    echo "--keep: stack left running (pids ${PIDS[*]}); backend http://127.0.0.1:$BACKEND_PORT"
    echo "stop it with: kill ${PIDS[*]}"
    exit "$code"
  fi
  for pid in "${PIDS[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  [ -z "${OMI_DEMO_STATE:-}" ] && rm -rf "$STATE"
  exit "$code"
}
trap teardown EXIT INT TERM

wait_port() {
  local name=$1 host=$2 port=$3 tries=${4:-90}
  for _ in $(seq 1 "$tries"); do
    port_busy "$host" "$port" && return 0
    sleep 1
  done
  echo "error: $name did not open $host:$port; logs in $STATE/logs" >&2
  tail -20 "$STATE/logs/$name.log" >&2 || true
  exit 1
}

redis-server --bind 127.0.0.1 --port "$REDIS_PORT" --dir "$STATE/redis" --save "" --appendonly no \
  > "$STATE/logs/redis.log" 2>&1 &
PIDS+=($!)

cp "$REPO/firestore.rules" "$REPO/firestore.indexes.json" "$STATE/emulators/"
cat > "$STATE/emulators/firebase.json" <<JSON
{
  "firestore": {"rules": "firestore.rules", "indexes": "firestore.indexes.json"},
  "emulators": {
    "firestore": {"port": $FIRESTORE_PORT, "host": "127.0.0.1"},
    "auth": {"port": $AUTH_PORT, "host": "127.0.0.1"},
    "hub": {"port": $HUB_PORT, "host": "127.0.0.1"},
    "logging": {"port": $((HUB_PORT + 100)), "host": "127.0.0.1"},
    "ui": {"enabled": false}
  }
}
JSON
(cd "$STATE/emulators" && exec "$FIREBASE_BIN" emulators:start --only firestore,auth --project demo-omi-local) \
  > "$STATE/logs/emulators.log" 2>&1 &
PIDS+=($!)

# ---- the family wiki: the real bridge plugin's /family-mcp handler ----
"$NODE_BIN" "$HERE/wiki_bridge_server.cjs" --repo "$LLMWIKI_REPO" --host "$LAN_IP" --port "$WIKI_PORT" \
  > "$STATE/logs/wiki.log" 2>&1 &
PIDS+=($!)

# ---- Hermes, Odysseus, Studio, Coinpile, Orb stand-ins ----
STUDIO_SECRET="studio-demo-webhook-secret"
"$PY" "$HERE/agents_stub.py" --host "$LAN_IP" --hermes-port "$HERMES_PORT" --odysseus-port "$ODYSSEUS_PORT" \
  --studio-port "$STUDIO_PORT" --coinpile-port "$COINPILE_PORT" --orb-port "$ORB_PORT" \
  --studio-secret "$STUDIO_SECRET" --studio-webhook "http://127.0.0.1:$BACKEND_PORT/v1/integrations/studio/events" \
  --log "$STATE/agents.jsonl" > "$STATE/logs/agents.log" 2>&1 &
PIDS+=($!)

wait_port redis 127.0.0.1 "$REDIS_PORT" 20
wait_port emulators 127.0.0.1 "$AUTH_PORT" 120
wait_port emulators 127.0.0.1 "$FIRESTORE_PORT" 60
wait_port wiki "$LAN_IP" "$WIKI_PORT" 20
wait_port agents "$LAN_IP" "$ORB_PORT" 20

# ---- backend: the env entrypoint.sh exports, on these ports ----
export STATE
export OMI_AUDIO_URL="${OMI_AUDIO_URL:-http://127.0.0.1:9}"
# shellcheck source=../audio-env.sh
. "$REPO/omi-host/audio-env.sh"
# shellcheck source=../auth-env.sh
. "$REPO/omi-host/auth-env.sh"
export OMI_ENV_STAGE=offline PROVIDER_MODE=offline
export FIRESTORE_EMULATOR_HOST="127.0.0.1:$FIRESTORE_PORT" FIREBASE_AUTH_EMULATOR_HOST="127.0.0.1:$AUTH_PORT"
export OMI_DISABLE_LOCAL_DEV_TOKEN_ROUTE=1 FIREBASE_AUTH_PROJECT_ID=demo-omi-local FIREBASE_PROJECT_ID=demo-omi-local
export GOOGLE_CLOUD_PROJECT=demo-omi-local FIRESTORE_DATABASE_ID="(default)"
export FIREBASE_API_KEY=local-firebase-auth-emulator-api-key MEMORY_ENABLED=on
export REDIS_DB_HOST=127.0.0.1 REDIS_DB_PORT="$REDIS_PORT" REDIS_DB_PASSWORD=
export ENVIRONMENT=local-dev-harness ENCRYPTION_SECRET=omi_local_dev_harness_32_byte_test_secret_not_prod
export ADMIN_KEY="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
export BASE_API_URL="http://127.0.0.1:$BACKEND_PORT" API_BASE_URL="http://127.0.0.1:$BACKEND_PORT"
export OMI_LLM_GATEWAY_FEATURE_MODE=off OMI_SHAPED_AGENT_MODE=on OMI_LOCAL_SCHEDULER=1 OMI_SELF_HOSTED=1
export OPENAI_API_KEY=sk-omi-local-harness-offline-openai-not-real
export GEMINI_API_KEY=omi-local-harness-offline-gemini-not-real
export ANTHROPIC_API_KEY=omi-local-harness-offline-anthropic-not-real
export PYTHONUNBUFFERED=1
# Connections: the operator allows exactly the stand-ins' LAN address; the queue works every 3 s
# and an approved day waits 6 s (instead of Approve all's 10-minute Undo window) so the run is short.
export OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS="$LAN_IP/32"
export OMI_OUTBOUND_TICK_SECONDS="${OMI_OUTBOUND_TICK_SECONDS:-3}"
export OMI_OUTBOUND_DAY_DELAY_SECONDS="${OMI_OUTBOUND_DAY_DELAY_SECONDS:-6}"
export STUDIO_WEBHOOK_SECRET="$STUDIO_SECRET"
# The container's egress guard, in block mode: only loopback and LAN destinations.
export OMI_EGRESS_GUARD=block OMI_EGRESS_LOG="$STATE/egress.jsonl"
export PYTHONPATH="$REPO/omi-host/egress-guard${PYTHONPATH:+:$PYTHONPATH}"
(cd "$REPO/backend" && exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT") \
  > "$STATE/logs/backend.log" 2>&1 &
PIDS+=($!)
wait_port backend 127.0.0.1 "$BACKEND_PORT" 180

SEED_CMD="cd '$REPO/backend' && PYTHONPATH=. '$PY' -"
set +e
"$PY" "$HERE/integrations_demo.py" \
  --backend "http://127.0.0.1:$BACKEND_PORT" \
  --seed-cmd "$SEED_CMD" \
  --lan "$LAN_IP" \
  --wiki-port "$WIKI_PORT" --hermes-port "$HERMES_PORT" --odysseus-port "$ODYSSEUS_PORT" \
  --studio-port "$STUDIO_PORT" --coinpile-port "$COINPILE_PORT" --orb-port "$ORB_PORT" \
  --agents-log "$STATE/agents.jsonl" --egress-log "$STATE/egress.jsonl" \
  "${DRIVER_ARGS[@]}"
RESULT=$?
set -e
[ "$RESULT" = 0 ] || echo "backend log: $STATE/logs/backend.log (kept only with --keep or OMI_DEMO_STATE)"
exit "$RESULT"
