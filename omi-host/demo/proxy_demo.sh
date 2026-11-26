#!/usr/bin/env bash
# Remote-access demo: the real backend behind a real NGINX running omi-host/proxy/nginx/omi.conf.
# See proxy_demo.py for the rows (chat streaming past an idle limit, /v4/listen, CalDAV, uploads).
#
# Starts the same local stack as caldav_demo.sh (redis, Firestore + Auth emulators, Radicale,
# the backend) with a slow stand-in model (slow_llm_stub.py) as its LLM, and two nginx in front:
#   :OMI_DEMO_PROXY_PORT (8480)  omi.conf as shipped, only the upstream addresses changed
#   :OMI_DEMO_IDLE_PORT  (8481)  the same with proxy_read_timeout = OMI_DEMO_IDLE_SECONDS (30),
#                                standing in for Cloudflare cutting a response idle ~100 s
# The model stays silent OMI_DEMO_LLM_DELAY (75) seconds; the backend's keep-alive interval is
# the production default (20 s) unless AGENT_STREAM_PROGRESS_HEARTBEAT_SECONDS is set.
# Cloudflare's own numbers: OMI_DEMO_IDLE_SECONDS=100 OMI_DEMO_LLM_DELAY=130.
#
#   OMI_RADICALE_BIN=<radicale> omi-host/demo/proxy_demo.sh [--keep]
#   --keep leaves everything running, e.g. to try omi-host/proxy/check_remote.sh against :8480.
#
# nginx: OMI_NGINX_BIN (default: nginx on PATH; `apt-get install nginx-light` works). The
# shipped-config row needs nginx 1.27.3+ (the compose image is 1.30); older ones skip it.
# Ports and other binaries: as caldav_demo.sh (OMI_DEMO_<NAME>_PORT, OMI_DEMO_PYTHON, FIREBASE_BIN),
# plus OMI_DEMO_PROXY_PORT, OMI_DEMO_IDLE_PORT, OMI_DEMO_LLM_PORT (8482).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
NGINX="${OMI_NGINX_BIN:-$(command -v nginx || true)}"
[ -x "$NGINX" ] || { echo "error: nginx not found; install it (apt-get install nginx-light) or set OMI_NGINX_BIN" >&2; exit 2; }
PY="${OMI_DEMO_PYTHON:-$REPO/backend/.venv/bin/python}"

PROXY_PORT="${OMI_DEMO_PROXY_PORT:-8480}"
IDLE_PORT="${OMI_DEMO_IDLE_PORT:-8481}"
LLM_PORT="${OMI_DEMO_LLM_PORT:-8482}"
IDLE_SECONDS="${OMI_DEMO_IDLE_SECONDS:-30}"
LLM_DELAY="${OMI_DEMO_LLM_DELAY:-75}"
export AGENT_STREAM_PROGRESS_HEARTBEAT_SECONDS="${AGENT_STREAM_PROGRESS_HEARTBEAT_SECONDS:-20}"
BACKEND_PORT="${OMI_DEMO_BACKEND_PORT:-8000}"
CALDAV_PORT="${OMI_DEMO_CALDAV_PORT:-5232}"

STATE="$(mktemp -d -t omi-proxy-demo.XXXXXX)"
echo "proxy demo state: $STATE"
PIDS=()
KEEP=0
for arg in "$@"; do [ "$arg" = --keep ] && KEEP=1; done
cleanup() {
  local code=$?
  if [ "$KEEP" = 1 ]; then
    echo "--keep: nginx on :$PROXY_PORT (shipped config) and :$IDLE_PORT (idle limit) and the model stub" \
         "left running (pids ${PIDS[*]}); try: bash omi-host/proxy/check_remote.sh http://127.0.0.1:$PROXY_PORT"
    exit "$code"
  fi
  for pid in "${PIDS[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  [ "$code" = 0 ] && rm -rf "$STATE" || echo "proxy demo logs kept: $STATE"
  exit "$code"
}
trap cleanup EXIT INT TERM

# ---- the slow model ----
"$PY" "$HERE/slow_llm_stub.py" --port "$LLM_PORT" --delay "$LLM_DELAY" --log "$STATE/llm.jsonl" \
  > "$STATE/llm.log" 2>&1 &
PIDS+=($!)

# ---- two nginx: omi.conf as shipped, and the same with a short idle limit ----
# run_nginx <name> <port> <idle seconds or empty>
run_nginx() {
  local name=$1 port=$2 idle=$3 root="$STATE/$1"
  mkdir -p "$root/conf.d" "$root/omi/extra" "$root/logs" "$root/tmp"
  sed "s/listen 8080/listen 127.0.0.1:$port/" "$REPO/omi-host/proxy/nginx/omi.conf" > "$root/conf.d/omi.conf"
  if [ -n "$idle" ]; then
    # Cloudflare stand-in: an idle limit, and the model reachable directly for the control row.
    sed -i "s/proxy_read_timeout 1300s;/proxy_read_timeout ${idle}s;/" "$root/conf.d/omi.conf"
    sed -i "0,/    location \/ {/s//    location \/__demo_llm\/ {\n        proxy_pass http:\/\/demo_llm\/;\n    }\n\n    location \/ {/" \
      "$root/conf.d/omi.conf"
  fi
  cat > "$root/omi/upstreams.conf" <<CONF
upstream omi_backend { server 127.0.0.1:$BACKEND_PORT; keepalive 16; }
upstream omi_caldav { server 127.0.0.1:$CALDAV_PORT; }
upstream demo_llm { server 127.0.0.1:$LLM_PORT; }
CONF
  {
    [ "$(id -u)" = 0 ] && echo "user root;"
    cat <<CONF
worker_processes 1;
daemon off;
pid logs/nginx.pid;
events { worker_connections 256; }
http {
    access_log logs/access.log;
    client_body_temp_path tmp/body;
    proxy_temp_path tmp/proxy;
    fastcgi_temp_path tmp/fastcgi;
    uwsgi_temp_path tmp/uwsgi;
    scgi_temp_path tmp/scgi;
    include conf.d/omi.conf;
}
CONF
  } > "$root/nginx.conf"
  "$NGINX" -t -q -p "$root" -c "$root/nginx.conf" -e "$root/logs/error.log"
  "$NGINX" -p "$root" -c "$root/nginx.conf" -e "$root/logs/error.log" &
  PIDS+=($!)
}
run_nginx nginx-main "$PROXY_PORT" ""
run_nginx nginx-idle "$IDLE_PORT" "$IDLE_SECONDS"

# ---- the backend stack, with the slow model as its local LLM ----
export OPENAI_BASE_URL="http://127.0.0.1:$LLM_PORT/v1" OMI_LOCAL_MODEL=stub-model LOCAL_LLM_MODEL=stub-model
export OMI_LOCAL_LLM_TIMEOUT_SECONDS="${OMI_LOCAL_LLM_TIMEOUT_SECONDS:-300}"
export OMI_DEMO_DRIVER=proxy_demo.py
set +e
"$HERE/caldav_demo.sh" \
  --proxy "http://127.0.0.1:$PROXY_PORT" --idle-proxy "http://127.0.0.1:$IDLE_PORT" \
  --idle-seconds "$IDLE_SECONDS" --keepalive-seconds "$AGENT_STREAM_PROGRESS_HEARTBEAT_SECONDS" \
  --llm-delay "$LLM_DELAY" --nginx-bin "$NGINX" --proxy-dir "$REPO/omi-host/proxy" --state "$STATE" "$@"
RESULT=$?
set -e
exit "$RESULT"
