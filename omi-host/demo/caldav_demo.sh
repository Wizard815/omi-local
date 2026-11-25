#!/usr/bin/env bash
# CalDAV demo: proves Omi sends AND receives tasks (VTODO) and calendar events
# (VEVENT) over CalDAV, end to end, against a real Radicale.
#
# Local mode (default): starts its own stack from this checkout, in a scratch
# state directory, the way omi-host/entrypoint.sh starts the container's:
#   redis, the Firestore + Auth emulators, Radicale (configured by
#   omi-host/caldav-env.sh), and the backend (uvicorn, OMI_SELF_HOSTED=1, the
#   in-process scheduler on, local login), plus a stub ALLGlance gateway so the
#   family-calendar approve path has somewhere to write.
# Then runs omi-host/demo/caldav_demo.py and tears everything down.
#
#   omi-host/demo/caldav_demo.sh            run, print the PASS/FAIL table, tear down
#   omi-host/demo/caldav_demo.sh --keep     leave the stack running afterwards
#
# Ports (override when another stack is using them):
#   OMI_DEMO_BACKEND_PORT=8000 OMI_DEMO_FIRESTORE_PORT=8085 OMI_DEMO_AUTH_PORT=9099
#   OMI_DEMO_CALDAV_PORT=5232 OMI_DEMO_REDIS_PORT=6390 OMI_DEMO_HUB_PORT=4410
#   OMI_DEMO_ALLGLANCE_PORT=8791
# Driver: OMI_DEMO_DRIVER (default caldav_demo.py; hermes_demo.sh runs hermes_demo.py on this stack).
# Binaries: OMI_RADICALE_BIN (radicale 3.5.x), OMI_DEMO_PYTHON (backend venv
# python, default backend/.venv/bin/python), FIREBASE_BIN (default the repo's
# node_modules/.bin/firebase; `npm ci` at the repo root installs it).
#
# Against the real Docker stack, run the driver directly instead (README.md).
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

BACKEND_PORT="${OMI_DEMO_BACKEND_PORT:-8000}"
FIRESTORE_PORT="${OMI_DEMO_FIRESTORE_PORT:-8085}"
AUTH_PORT="${OMI_DEMO_AUTH_PORT:-9099}"
CALDAV_PORT="${OMI_DEMO_CALDAV_PORT:-5232}"
REDIS_PORT="${OMI_DEMO_REDIS_PORT:-6390}"
HUB_PORT="${OMI_DEMO_HUB_PORT:-4410}"
ALLGLANCE_PORT="${OMI_DEMO_ALLGLANCE_PORT:-8791}"
PY="${OMI_DEMO_PYTHON:-$REPO/backend/.venv/bin/python}"
FIREBASE_BIN="${FIREBASE_BIN:-$REPO/node_modules/.bin/firebase}"
export OMI_RADICALE_BIN="${OMI_RADICALE_BIN:-/opt/radicale/bin/radicale}"

STATE="${OMI_DEMO_STATE:-$(mktemp -d -t omi-caldav-demo.XXXXXX)}"
mkdir -p "$STATE/logs" "$STATE/redis" "$STATE/emulators"
echo "demo state: $STATE"

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for spec in "backend:$BACKEND_PORT" "firestore:$FIRESTORE_PORT" "auth:$AUTH_PORT" "caldav:$CALDAV_PORT" \
            "redis:$REDIS_PORT" "hub:$HUB_PORT" "allglance:$ALLGLANCE_PORT"; do
  if port_busy "${spec#*:}"; then
    name="${spec%%:*}"; name="${name//-/_}"
    echo "error: port ${spec#*:} (${spec%%:*}) is in use; set OMI_DEMO_${name^^}_PORT (see the header)" >&2
    exit 2
  fi
done
for bin in "$PY" "$FIREBASE_BIN" "$OMI_RADICALE_BIN"; do
  [ -x "$bin" ] || { echo "error: $bin missing (see the header)" >&2; exit 2; }
done
command -v redis-server >/dev/null || { echo "error: redis-server missing" >&2; exit 2; }

PIDS=()
teardown() {
  local code=$?
  if [ "$KEEP" = "1" ]; then
    echo "--keep: stack left running (pids ${PIDS[*]}); backend http://127.0.0.1:$BACKEND_PORT," \
         "caldav http://127.0.0.1:$CALDAV_PORT/ user $CALDAV_USER password in $STATE/secrets/caldav_password"
    echo "stop it with: kill ${PIDS[*]}"
    exit "$code"
  fi
  # TERM, not INT: background jobs of a non-interactive shell ignore SIGINT.
  for pid in "${PIDS[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  [ -z "${OMI_DEMO_STATE:-}" ] && rm -rf "$STATE"
  exit "$code"
}
trap teardown EXIT INT TERM

wait_port() {
  local name=$1 port=$2 tries=${3:-90}
  for _ in $(seq 1 "$tries"); do
    port_busy "$port" && return 0
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

# ---- Radicale, configured exactly as the container configures it ----
export CALDAV_DEFAULT_URL="http://127.0.0.1:$CALDAV_PORT/"
CALDAV_PASSWORD="${CALDAV_PASSWORD:-}"
# shellcheck source=../caldav-env.sh
. "$REPO/omi-host/caldav-env.sh"
# caldav-env.sh writes hosts = 0.0.0.0:5232; the command line takes precedence.
"$OMI_RADICALE_BIN" --config "$OMI_CALDAV_DIR/config" --server-hosts "127.0.0.1:$CALDAV_PORT" \
  > "$STATE/logs/radicale.log" 2>&1 &
PIDS+=($!)

# ---- stub ALLGlance gateway (family calendar approve writes here) ----
"$PY" "$HERE/allglance_stub.py" --port "$ALLGLANCE_PORT" --log "$STATE/allglance-stub.jsonl" \
  > "$STATE/logs/allglance-stub.log" 2>&1 &
PIDS+=($!)

wait_port redis "$REDIS_PORT" 20
wait_port emulators "$AUTH_PORT" 120
wait_port emulators "$FIRESTORE_PORT" 60
wait_port radicale "$CALDAV_PORT" 30
wait_port allglance-stub "$ALLGLANCE_PORT" 20

# ---- backend: the env entrypoint.sh exports, on these ports ----
export STATE
# Live audio is not part of this demo; audio-env.sh only needs a URL to configure the chain.
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
export CALDAV_SYNC_SECONDS="${CALDAV_SYNC_SECONDS:-60}"
export OPENAI_API_KEY=sk-omi-local-harness-offline-openai-not-real
export GEMINI_API_KEY=omi-local-harness-offline-gemini-not-real
export ANTHROPIC_API_KEY=omi-local-harness-offline-anthropic-not-real
export PYTHONUNBUFFERED=1
(cd "$REPO/backend" && exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$BACKEND_PORT") \
  > "$STATE/logs/backend.log" 2>&1 &
PIDS+=($!)
wait_port backend "$BACKEND_PORT" 180

# Seeding runs in-process with the backend's own modules and environment.
SEED_CMD="cd '$REPO/backend' && PYTHONPATH=. '$PY' -"
set +e
"$PY" "$HERE/${OMI_DEMO_DRIVER:-caldav_demo.py}" \
  --backend "http://127.0.0.1:$BACKEND_PORT" \
  --caldav "http://127.0.0.1:$CALDAV_PORT/" --caldav-user "$CALDAV_USER" --caldav-password "$CALDAV_PASSWORD" \
  --seed-cmd "$SEED_CMD" \
  --allglance-url "http://127.0.0.1:$ALLGLANCE_PORT" --allglance-log "$STATE/allglance-stub.jsonl" \
  --scheduler-wait 45 \
  "${DRIVER_ARGS[@]}"
RESULT=$?
set -e
[ "$RESULT" = 0 ] || echo "backend log: $STATE/logs/backend.log (kept only with --keep or OMI_DEMO_STATE)"
exit "$RESULT"
