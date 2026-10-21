# Login for the omi-local backend. Sourced by entrypoint.sh; also sourced
# directly by omi-host/test_auth_env.py.
#
# Every sign-in, on the LAN or through a tunnel, goes through
# /v1/auth/local-login, which returns a session token signed with
# LOCAL_AUTH_JWT_SECRET (backend/utils/local_auth.py). The Firebase Auth
# emulator's own tokens are unsigned and refused
# (backend/utils/self_hosted_id_tokens.py), and its port stays inside the
# container.
#
# Unless you set LOCAL_AUTH_JWT_SECRET yourself, one is generated on first start
# and kept on the data volume, so sessions survive restarts. Deleting
# $STATE/secrets/local_auth_jwt_secret signs every device out.

if [ -z "${LOCAL_AUTH_JWT_SECRET:-}" ]; then
  _omi_secret_dir="${STATE:-/data}/secrets"
  _omi_secret_file="$_omi_secret_dir/local_auth_jwt_secret"
  if [ ! -d "$_omi_secret_dir" ]; then
    mkdir -p -m 700 "$_omi_secret_dir"
  fi
  if [ ! -s "$_omi_secret_file" ]; then
    # Owner-only before the first byte, and never a half-written file.
    (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$_omi_secret_file.tmp" \
      && mv "$_omi_secret_file.tmp" "$_omi_secret_file")
  fi
  LOCAL_AUTH_JWT_SECRET="$(cat "$_omi_secret_file")"
  if [ -z "$LOCAL_AUTH_JWT_SECRET" ]; then
    echo "error: could not create $_omi_secret_file; set LOCAL_AUTH_JWT_SECRET" >&2
    exit 1
  fi
  echo "login: session tokens signed with the key in $_omi_secret_file"
else
  echo "login: session tokens signed with LOCAL_AUTH_JWT_SECRET"
fi
export LOCAL_AUTH_JWT_SECRET
