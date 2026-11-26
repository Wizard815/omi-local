#!/usr/bin/env bash
# Home Assistant demo: Omi fires events into Home Assistant, an agent reads the house over MCP, a
# light call waits for the user's OK and then runs, a lock call is refused. ha_demo.py has the rows.
#
# Starts its own stack from this checkout in a scratch directory: redis, the Firestore + Auth
# emulators, fake_home_assistant.py (a small stand-in, not a real Home Assistant), and the backend
# (uvicorn, offline providers, the in-process scheduler on, local login) with OMI_HA_URL and
# OMI_HA_TOKEN pointing at the fake. Then runs ha_demo.py and tears everything down.
#
#   omi-host/demo/ha_demo.sh            run, print the PASS/FAIL table, tear down
#   omi-host/demo/ha_demo.sh --keep     leave the stack running afterwards
#
# Omi refuses loopback for outbound calls, so the fake listens on this machine's LAN address
# (OMI_DEMO_HA_HOST, default the first of `hostname -I`), which is allowed for this run through
# OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS, exactly as a real Home Assistant on the LAN is.
#
# Ports: OMI_DEMO_BACKEND_PORT=8000 OMI_DEMO_FIRESTORE_PORT=8085 OMI_DEMO_AUTH_PORT=9099
#        OMI_DEMO_REDIS_PORT=6390 OMI_DEMO_HUB_PORT=4410 OMI_DEMO_HA_PORT=8123
# Binaries: OMI_DEMO_PYTHON (default backend/.venv/bin/python), FIREBASE_BIN (default the repo's
# node_modules/.bin/firebase; `npm ci` at the repo root installs it), redis-server, Java 21.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
KEEP=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    *) echo "error: unknown argument $arg" >&2; exit 2 ;;
  esac
done

BACKEND_PORT="${OMI_DEMO_BACKEND_PORT:-8000}"
FIRESTORE_PORT="${OMI_DEMO_FIRESTORE_PORT:-8085}"
AUTH_PORT="${OMI_DEMO_AUTH_PORT:-9099}"
REDIS_PORT="${OMI_DEMO_REDIS_PORT:-6390}"
HUB_PORT="${OMI_DEMO_HUB_PORT:-4410}"
HA_PORT="${OMI_DEMO_HA_PORT:-8123}"
HA_HOST="${OMI_DEMO_HA_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
PY="${OMI_DEMO_PYTHON:-$REPO/backend/.venv/bin/python}"
FIREBASE_BIN="${FIREBASE_BIN:-$REPO/node_modules/.bin/firebase}"
[ -n "$HA_HOST" ] || { echo "error: no LAN address found; set OMI_DEMO_HA_HOST" >&2; exit 2; }

STATE="${OMI_DEMO_STATE:-$(mktemp -d -t omi-ha-demo.XXXXXX)}"
mkdir -p "$STATE/logs" "$STATE/redis" "$STATE/emulators"
echo "demo state: $STATE"

port_busy() { (exec 3<>"/dev/tcp/${2:-127.0.0.1}/$1") 2>/dev/null; }
for spec in "backend:$BACKEND_PORT" "firestore:$FIRESTORE_PORT" "auth:$AUTH_PORT" "redis:$REDIS_PORT" \
            "hub:$HUB_PORT" "ha:$HA_PORT"; do
  if port_busy "${spec#*:}"; then
    echo "error: port ${spec#*:} (${spec%%:*}) is in use; set OMI_DEMO_${spec%%:*}_PORT (see the header)" >&2
    exit 2
  fi
done
for bin in "$PY" "$FIREBASE_BIN"; do
  [ -x "$bin" ] || { echo "error: $bin missing (see the header)" >&2; exit 2; }
done
command -v redis-server >/dev/null || { echo "error: redis-server missing" >&2; exit 2; }

PIDS=()
teardown() {
  local code=$?
  if [ "$KEEP" = "1" ]; then
    echo "--keep: stack left running (pids ${PIDS[*]}); backend http://127.0.0.1:$BACKEND_PORT," \
         "fake Home Assistant http://$HA_HOST:$HA_PORT (its log: /_demo/log)"
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
  local name=$1 port=$2 tries=${3:-90} host=${4:-127.0.0.1}
  for _ in $(seq 1 "$tries"); do
    port_busy "$port" "$host" && return 0
    sleep 1
  done
  echo "error: $name did not open port $port; logs in $STATE/logs" >&2
  tail -20 "$STATE/logs/$name.log" >&2 || true
  exit 1
}

# ---- redis ----
redis-server --bind 127.0.0.1 --port "$REDIS_PORT" --dir "$STATE/redis" --save "" --appendonly no \
  > "$STATE/logs/redis.log" 2>&1 &
PIDS+=($!)

# ---- Firestore + Auth emulators (ports parametrized, UI off) ----
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

# ---- the fake Home Assistant, on the LAN address ----
HA_TOKEN="$("$PY" -c 'import secrets; print("demo-ha-" + secrets.token_urlsafe(24))')"
"$PY" "$HERE/fake_home_assistant.py" --host "$HA_HOST" --port "$HA_PORT" --token "$HA_TOKEN" \
  > "$STATE/logs/ha.log" 2>&1 &
PIDS+=($!)

wait_port redis "$REDIS_PORT" 20
wait_port emulators "$AUTH_PORT" 120
wait_port emulators "$FIRESTORE_PORT" 60
wait_port ha "$HA_PORT" 20 "$HA_HOST"

# ---- backend: the env entrypoint.sh exports, plus Home Assistant ----
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
# Home Assistant, as omi-host/HOME-ASSISTANT.md sets it up (defaults for the allowlists).
export OMI_HA_URL="http://$HA_HOST:$HA_PORT" OMI_HA_TOKEN="$HA_TOKEN"
export OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS="$HA_HOST/32"
(cd "$REPO/backend" && exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT") \
  > "$STATE/logs/backend.log" 2>&1 &
PIDS+=($!)
wait_port backend "$BACKEND_PORT" 180

# Seeding runs in-process with the backend's own modules and environment.
SEED_CMD="cd '$REPO/backend' && PYTHONPATH=. '$PY' -"
set +e
"$PY" "$HERE/ha_demo.py" --backend "http://127.0.0.1:$BACKEND_PORT" --seed-cmd "$SEED_CMD" \
  --ha-url "$OMI_HA_URL" --ha-token "$HA_TOKEN"
RESULT=$?
set -e
echo "backend log lines about Home Assistant actions:"
grep -h 'home_assistant: action' "$STATE/logs/backend.log" | sed -E 's/^.*(home_assistant: action)/  \1/' || true
[ "$RESULT" = 0 ] || echo "backend log: $STATE/logs/backend.log (kept only with --keep or OMI_DEMO_STATE)"
exit "$RESULT"
