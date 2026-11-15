# The stack's own CalDAV server (Radicale), run inside the omi-local container.
# Sourced by entrypoint.sh; also sourced directly by omi-host/test_caldav_env.py.
#
# Sourcing sets the backend's default CalDAV account and writes Radicale's
# config; omi_caldav_start then starts the server in the background. It used to
# be its own container (omi-radicale); one process here does the same job.
#
#   OMI_BUNDLED_CALDAV=0   no bundled server (use your own: set CALDAV_DEFAULT_URL)
#   CALDAV_USER            the account (default omi)
#   CALDAV_PASSWORD        its password; unset, one is generated on first start
#                          and kept in $STATE/secrets/caldav_password
#   CALDAV_DEFAULT_URL     unset: this server, http://127.0.0.1:5232/
#
# Phones reach it at http://<host>:${CALDAV_PORT:-5232}/ (DAVx5, Tasks.org).
# Calendars live in $STATE/radicale/collections, on the data volume, so the
# volume backups in maintenance.sh cover them. On the first start after the
# move, a mounted copy of the old omi-radicale volume at /radicale-legacy is
# copied in once.

OMI_CALDAV_DIR="${STATE:-/data}/radicale"
OMI_RADICALE_BIN="${OMI_RADICALE_BIN:-/opt/radicale/bin/radicale}"
OMI_RADICALE_LEGACY="${OMI_RADICALE_LEGACY:-/radicale-legacy}"
CALDAV_USER="${CALDAV_USER:-omi}"

if [ "${OMI_BUNDLED_CALDAV:-1}" = "0" ]; then
  echo "caldav: bundled server off (OMI_BUNDLED_CALDAV=0)"
  OMI_CALDAV_ENABLED=0
else
  OMI_CALDAV_ENABLED=1
  if [ -z "${CALDAV_PASSWORD:-}" ]; then
    _omi_secret_dir="${STATE:-/data}/secrets"
    _omi_caldav_secret="$_omi_secret_dir/caldav_password"
    if [ ! -d "$_omi_secret_dir" ]; then
      mkdir -p -m 700 "$_omi_secret_dir"
    fi
    if [ ! -s "$_omi_caldav_secret" ]; then
      (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$_omi_caldav_secret.tmp" \
        && mv "$_omi_caldav_secret.tmp" "$_omi_caldav_secret")
    fi
    CALDAV_PASSWORD="$(cat "$_omi_caldav_secret")"
    if [ -z "$CALDAV_PASSWORD" ]; then
      echo "error: could not create $_omi_caldav_secret; set CALDAV_PASSWORD" >&2
      exit 1
    fi
    echo "caldav: user '$CALDAV_USER', password in $_omi_caldav_secret"
  else
    echo "caldav: user '$CALDAV_USER', password from CALDAV_PASSWORD"
  fi

  mkdir -p "$OMI_CALDAV_DIR"
  # Calendars from the old omi-radicale container, once.
  if [ ! -d "$OMI_CALDAV_DIR/collections" ] && [ -d "$OMI_RADICALE_LEGACY/collections" ]; then
    cp -a "$OMI_RADICALE_LEGACY/collections" "$OMI_CALDAV_DIR/collections" \
      && echo "caldav: copied the calendars from the old omi-radicale volume"
  fi
  mkdir -p "$OMI_CALDAV_DIR/collections"
  # One user, plain htpasswd, owner-only (the password is in it in clear).
  (umask 077 && printf '%s:%s\n' "$CALDAV_USER" "$CALDAV_PASSWORD" > "$OMI_CALDAV_DIR/users")
  cat > "$OMI_CALDAV_DIR/config" <<CONF
[server]
hosts = 0.0.0.0:5232
[auth]
type = htpasswd
htpasswd_filename = $OMI_CALDAV_DIR/users
htpasswd_encryption = plain
[storage]
filesystem_folder = $OMI_CALDAV_DIR/collections
[rights]
type = owner_only
CONF

  # The backend's default account: this server, unless the operator points
  # the whole stack somewhere else (Nextcloud, the ALLGlance gateway's /dav/).
  CALDAV_DEFAULT_URL="${CALDAV_DEFAULT_URL:-http://127.0.0.1:5232/}"
fi
# The default account's login is CALDAV_USER / CALDAV_PASSWORD, as compose
# always passed it, whichever server CALDAV_DEFAULT_URL names.
CALDAV_DEFAULT_USERNAME="${CALDAV_DEFAULT_USERNAME:-$CALDAV_USER}"
CALDAV_DEFAULT_PASSWORD="${CALDAV_DEFAULT_PASSWORD:-${CALDAV_PASSWORD:-}}"
export CALDAV_USER
[ -n "${CALDAV_PASSWORD:-}" ] && export CALDAV_PASSWORD
[ -n "${CALDAV_DEFAULT_URL:-}" ] && export CALDAV_DEFAULT_URL
[ -n "${CALDAV_DEFAULT_USERNAME:-}" ] && export CALDAV_DEFAULT_USERNAME
[ -n "${CALDAV_DEFAULT_PASSWORD:-}" ] && export CALDAV_DEFAULT_PASSWORD

# Starts Radicale in the background and sets RADICALE_PID; a no-op when off.
omi_caldav_start() {
  [ "$OMI_CALDAV_ENABLED" = "1" ] || return 0
  if [ ! -x "$OMI_RADICALE_BIN" ]; then
    echo "caldav: $OMI_RADICALE_BIN missing; tasks and calendar sync stay off" >&2
    return 0
  fi
  "$OMI_RADICALE_BIN" --config "$OMI_CALDAV_DIR/config" > "${STATE:-/data}/logs/radicale.log" 2>&1 &
  RADICALE_PID=$!
  echo "caldav: radicale pid $RADICALE_PID on :5232"
}
