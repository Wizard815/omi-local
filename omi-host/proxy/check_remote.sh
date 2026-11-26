#!/usr/bin/env bash
# Check an Omi server from OUTSIDE your network, through the Cloudflare Tunnel (or Tailscale):
# the same things omi-host/demo/proxy_demo.sh proves locally, against the real hostname.
# Run it from a phone hotspot or any machine that is not on the server's LAN.
#
#   bash omi-host/proxy/check_remote.sh https://omi.example.com
#   OMI_USER=you OMI_PASSWORD=... bash omi-host/proxy/check_remote.sh https://omi.example.com
#   OMI_USER=you OMI_PASSWORD=... CALDAV_USER=omi CALDAV_PASSWORD=... bash omi-host/proxy/check_remote.sh https://omi.example.com
#
# Without a login it checks reachability, TLS, Omi's own auth, the dashboard gate and the
# upload size limit. With OMI_USER/OMI_PASSWORD it also logs in, streams one chat answer
# (timing every byte: no silence may be longer than the keep-alive) and opens /v4/listen.
# With CALDAV_USER/CALDAV_PASSWORD it runs a CalDAV PROPFIND at /caldav/.
#
# Side effects: one chat message ("Reply with one word: ok") in your chat history, one
# login. Uploads are sent WITHOUT a login, so the server reads and refuses them: nothing
# is stored. Needs curl and python3.
#   OMI_CHECK_PROMPT="..."   ask something else (e.g. a long question, to watch keep-alives)
#   OMI_CHECK_UPLOAD_MB=90   the upload that must pass (0 skips both upload checks)
set -uo pipefail

BASE="${1:-}"
[ -n "$BASE" ] || { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
BASE="${BASE%/}"
UPLOAD_MB="${OMI_CHECK_UPLOAD_MB:-90}"
PROMPT="${OMI_CHECK_PROMPT:-Reply with one word: ok}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS=0 FAIL=0 WARN=0

ok()   { PASS=$((PASS + 1)); printf 'PASS  %-66s %s\n' "$1" "${2:-}"; }
bad()  { FAIL=$((FAIL + 1)); printf 'FAIL  %-66s %s\n' "$1" "${2:-}"; }
warn() { WARN=$((WARN + 1)); printf 'WARN  %-66s %s\n' "$1" "${2:-}"; }

# ---- reachability, TLS, who answers ----
code=$(curl -sS -o "$TMP/health" -D "$TMP/health.h" -w '%{http_code}' --max-time 30 "$BASE/v1/health" 2>"$TMP/err") || true
if [ "$code" = 200 ]; then ok "GET /v1/health over HTTPS" "HTTP 200"; else bad "GET /v1/health over HTTPS" "HTTP ${code:-none} $(head -c 200 "$TMP/err")"; fi
if grep -qi '^server: cloudflare' "$TMP/health.h"; then
  ok "answered through Cloudflare" "$(grep -i '^cf-ray' "$TMP/health.h" | tr -d '\r')"
else
  warn "not answered by Cloudflare (fine for Tailscale)" "$(grep -i '^server' "$TMP/health.h" | tr -d '\r')"
fi

# ---- Omi's own auth stays on; the dashboard is gated ----
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$BASE/v1/users/me/local-usage")
if [ "$code" = 401 ] || [ "$code" = 403 ]; then ok "the API refuses a caller without a login" "HTTP $code"; else bad "the API refuses a caller without a login" "HTTP $code"; fi
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$BASE/")
case "$code" in
  401) ok "the dashboard asks for a password" "HTTP 401" ;;
  302|303|307) warn "the dashboard redirects (Cloudflare Access?)" "HTTP $code" ;;
  *) bad "the dashboard asks for a password (set DASHBOARD_USERNAME/PASSWORD)" "HTTP $code" ;;
esac

# ---- upload size: under the limit reaches the server, over it is refused at the edge ----
upload() {  # <MB> -> "http_code server-header"
  head -c "$(( $1 * 1024 * 1024 ))" /dev/zero > "$TMP/blob.bin"
  curl -s -o "$TMP/up.body" -D "$TMP/up.h" -w '%{http_code}' --max-time 600 \
    -F "files=@$TMP/blob.bin;filename=audio_check_opus_16000_1_fs160_1700000000.bin" \
    "$BASE/v2/sync-local-files"
  rm -f "$TMP/blob.bin"
}
if [ "$UPLOAD_MB" != 0 ]; then
  code=$(upload "$UPLOAD_MB")
  if { [ "$code" = 401 ] || [ "$code" = 403 ]; } && grep -q '"detail"' "$TMP/up.body"; then
    ok "a ${UPLOAD_MB} MB upload reaches the Omi server" "HTTP $code from the backend (refused: no login, nothing stored)"
  else
    bad "a ${UPLOAD_MB} MB upload reaches the Omi server" "HTTP $code: $(head -c 120 "$TMP/up.body")"
  fi
  code=$(upload 110)
  if [ "$code" = 413 ]; then
    ok "a 110 MB upload is refused at the edge (Cloudflare plan limit)" "HTTP 413"
  elif [ "$code" = 401 ] || [ "$code" = 403 ]; then
    warn "a 110 MB upload reached the server (no 100 MB edge limit here)" "HTTP $code"
  else
    warn "a 110 MB upload" "HTTP $code"
  fi
fi

# ---- with a login: chat stream timing and the live-audio WebSocket ----
if [ -n "${OMI_USER:-}" ] && [ -n "${OMI_PASSWORD:-}" ]; then
  TOKEN=$(python3 -c 'import json,sys; print(json.dumps({"username": sys.argv[1], "password": sys.argv[2]}))' \
      "$OMI_USER" "$OMI_PASSWORD" \
    | curl -s --max-time 30 -H 'Content-Type: application/json' --data-binary @- "$BASE/v1/auth/local-login" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_token",""))' 2>/dev/null)
  if [ -n "$TOKEN" ]; then ok "log in (/v1/auth/local-login)"; else bad "log in (/v1/auth/local-login)" "check OMI_USER/OMI_PASSWORD"; fi
fi
if [ -n "${TOKEN:-}" ]; then
  python3 - "$BASE" "$TOKEN" "$PROMPT" > "$TMP/chat.out" <<'PY'
import base64, http.client, json, ssl, sys, time
from urllib.parse import urlsplit
base, token, prompt = sys.argv[1:4]
u = urlsplit(base)
conn = (http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection)(u.hostname, u.port, timeout=1300)
start = time.monotonic()
conn.request('POST', '/v2/messages', body=json.dumps({'text': prompt}),
             headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'X-Omi-Chat-Failure-Protocol': '1'})
resp = conn.getresponse()
times, raw = [], b''
while True:
    try:
        chunk = resp.read1(65536)
    except Exception as error:
        print('error', type(error).__name__); break
    if not chunk:
        break
    times.append(time.monotonic() - start); raw += chunk
lines = [l for l in raw.decode('utf-8', 'replace').split('\n') if l.strip()]
gaps = [b - a for a, b in zip([0.0] + times, times)]
done = [l for l in lines if l.startswith('done: ')]
text = json.loads(base64.b64decode(done[0][6:])).get('text', '') if done else ''
print('status', resp.status)
print('total', round(time.monotonic() - start, 1))
print('maxgap', round(max(gaps) if gaps else -1, 1))
print('keepalives', sum(1 for l in lines if l.startswith('think: Still working')))
print('done', 1 if done else 0)
print('text', text[:60].replace('\n', ' '))
PY
  get() { awk -v k="$1" '$1 == k { $1 = ""; sub(/^ /, ""); print }' "$TMP/chat.out"; }
  if [ "$(get status)" = 200 ] && [ "$(get done)" = 1 ]; then
    ok "a chat answer streams back (/v2/messages)" "$(get total) s: $(get text)"
  else
    bad "a chat answer streams back (/v2/messages)" "$(tr '\n' ' ' < "$TMP/chat.out")"
  fi
  gap=$(get maxgap)
  if python3 -c "import sys; sys.exit(0 if 0 <= float('${gap:--1}') <= 30 else 1)"; then
    ok "no silence over 30 s in the stream (keep-alive every 20 s)" "longest ${gap} s, $(get keepalives) keep-alives"
  else
    bad "no silence over 30 s in the stream (keep-alive every 20 s)" "longest ${gap} s"
  fi
  # WebSocket upgrade: only the 101 is checked (no audio is sent).
  host=$(printf '%s' "$BASE" | sed -E 's#^[a-z]+://##')
  wsline=$(curl -s -i --http1.1 --max-time 8 -o - \
      -H "Authorization: Bearer $TOKEN" -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
      -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
      "$BASE/v4/listen?language=en&sample_rate=16000&codec=pcm16&channels=1" 2>/dev/null | head -1 | tr -d '\r')
  case "$wsline" in
    *" 101"*) ok "/v4/listen upgrades to a WebSocket" "$wsline" ;;
    *) bad "/v4/listen upgrades to a WebSocket" "${wsline:-no response} (wss://$host/v4/listen)" ;;
  esac
fi

# ---- CalDAV ----
if [ -n "${CALDAV_USER:-}" ] && [ -n "${CALDAV_PASSWORD:-}" ]; then
  code=$(curl -s -o "$TMP/dav" -w '%{http_code}' --max-time 30 -X PROPFIND -H 'Depth: 1' \
    -u "$CALDAV_USER:$CALDAV_PASSWORD" "$BASE/caldav/$CALDAV_USER/")
  if [ "$code" = 207 ]; then ok "CalDAV PROPFIND at /caldav/" "HTTP 207"; else bad "CalDAV PROPFIND at /caldav/" "HTTP $code"; fi
fi

echo
echo "$PASS passed, $FAIL failed, $WARN to look at"
[ "$FAIL" = 0 ]
