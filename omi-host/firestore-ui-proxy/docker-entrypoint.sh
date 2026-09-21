#!/bin/sh
# Generates nginx's htpasswd file from FIRESTORE_UI_USERNAME/PASSWORD at
# startup rather than baking credentials into the image, so .env stays the
# single source of truth (and changing the password just needs a restart,
# not a rebuild). Refuses to start rather than serve the Firestore data
# browser unauthenticated — this proxy exists specifically because that UI
# has no auth of its own.
set -eu

if [ -z "${FIRESTORE_UI_USERNAME:-}" ] || [ -z "${FIRESTORE_UI_PASSWORD:-}" ]; then
  echo "ERROR: FIRESTORE_UI_USERNAME and FIRESTORE_UI_PASSWORD must both be set." >&2
  echo "This proxy exists to put HTTP Basic Auth in front of the Firestore" >&2
  echo "Emulator UI, which has no authentication of its own. Refusing to" >&2
  echo "start unauthenticated. Set both in omi-host/.env." >&2
  exit 1
fi

htpasswd -cb /etc/nginx/.htpasswd "$FIRESTORE_UI_USERNAME" "$FIRESTORE_UI_PASSWORD" > /dev/null

exec nginx -g 'daemon off;'
