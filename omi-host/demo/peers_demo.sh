#!/usr/bin/env bash
# ALLGlance and FAITH read Omi with their own MCP clients (peers_demo.py has the rows).
# Starts the caldav_demo.sh stack and runs peers_demo.py on it.
#
#   OMI_ALLGLANCE_REPO=<ALLGlance checkout> OMI_FAITH_SERVER=<copy of F.A.I.T.H/server with
#   node_modules (npm install in the copy)> OMI_RADICALE_BIN=<radicale> omi-host/demo/peers_demo.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "${OMI_ALLGLANCE_REPO:-}/gateway/src/adapters/OmiAdapter.js" ] || { echo "error: set OMI_ALLGLANCE_REPO" >&2; exit 2; }
[ -x "${OMI_FAITH_SERVER:-}/node_modules/.bin/tsx" ] || [ -x "${OMI_FAITH_SERVER:-}/../node_modules/.bin/tsx" ] \
  || { echo "error: set OMI_FAITH_SERVER to a FAITH server copy with node_modules (npm install in the copy)" >&2; exit 2; }
export OMI_ALLGLANCE_REPO OMI_FAITH_SERVER OMI_DEMO_DRIVER=peers_demo.py
exec "$HERE/caldav_demo.sh" "$@"
