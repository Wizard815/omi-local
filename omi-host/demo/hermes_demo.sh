#!/usr/bin/env bash
# Hermes demo: Hermes (hermes-agent) uses Omi over MCP, Omi hands work to Hermes, and every
# write waits for the user's OK before it reaches CalDAV. See hermes_demo.py for the rows.
#
# Starts the same local stack as caldav_demo.sh (redis, Firestore + Auth emulators, Radicale,
# the backend) and runs hermes_demo.py on it with hermes-agent's own MCP client.
#
#   OMI_HERMES_PYTHON=<hermes venv>/bin/python OMI_HERMES_SRC=<hermes-agent checkout> \
#   OMI_RADICALE_BIN=<radicale> omi-host/demo/hermes_demo.sh [--keep]
#
# Send to agent against a real hermes-agent API server (optional; it spends model tokens):
#   OMI_HERMES_URL=http://<lan-ip>:8642 OMI_HERMES_KEY=<API_SERVER_KEY>
# The URL must be a LAN address (Omi refuses loopback for outbound calls); it is allowed
# through OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS for this run.
#
# Ports and binaries: as caldav_demo.sh (OMI_DEMO_<NAME>_PORT, OMI_DEMO_PYTHON, FIREBASE_BIN).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -x "${OMI_HERMES_PYTHON:-}" ] || { echo "error: set OMI_HERMES_PYTHON to a hermes-agent venv python" >&2; exit 2; }
[ -f "${OMI_HERMES_SRC:-}/model_tools.py" ] || { echo "error: set OMI_HERMES_SRC to a hermes-agent checkout" >&2; exit 2; }
export OMI_HERMES_PYTHON OMI_HERMES_SRC
export OMI_DEMO_DRIVER=hermes_demo.py
export OMI_OUTBOUND_TICK_SECONDS="${OMI_OUTBOUND_TICK_SECONDS:-3}"
if [ -n "${OMI_HERMES_URL:-}" ]; then
  host="$(printf '%s' "$OMI_HERMES_URL" | sed -E 's#^[a-z]+://([^:/]+).*#\1#')"
  export OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS="${OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS:-$host/32}"
  export OMI_HERMES_URL OMI_HERMES_KEY
fi
exec "$HERE/caldav_demo.sh" "$@"
