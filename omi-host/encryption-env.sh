# ENCRYPTION_SECRET for the omi-local backend. Sourced by entrypoint.sh; also sourced
# directly by omi-host/test_encryption_env.py.
#
# backend/utils/encryption.py derives the key that encrypts stored data from it,
# and other code signs ids and cursors with it. It used to default to a string
# published in this repo, so every install that set nothing shared one key.
#
# In order:
#   1. ENCRYPTION_SECRET from .env always wins.
#   2. Else the key kept on the data volume ($STATE/secrets/encryption_secret).
#   3. Else, on a data volume that has been used before, the old published
#      default, with a warning: that data was written under it and a new key
#      would make it unreadable.
#   4. Else (a new install) a random key is generated and kept on the volume.
#
# Back up $STATE/secrets with the data: without the key the data cannot be read.

_OMI_PUBLISHED_ENCRYPTION_SECRET="omi_local_dev_harness_32_byte_test_secret_not_prod"

_omi_volume_used() {
  # A volume that has been started before has the login key (auth-env.sh) or
  # exported Firestore or Redis data on it.
  local dir
  [ -e "${STATE:-/data}/secrets" ] && return 0
  for dir in firebase-export redis; do
    if [ -d "${STATE:-/data}/$dir" ] && [ -n "$(ls -A "${STATE:-/data}/$dir" 2>/dev/null)" ]; then
      return 0
    fi
  done
  return 1
}

if [ -n "${ENCRYPTION_SECRET:-}" ]; then
  if [ "$ENCRYPTION_SECRET" = "$_OMI_PUBLISHED_ENCRYPTION_SECRET" ]; then
    echo "WARNING: ENCRYPTION_SECRET is the value published in the omi-local repo." >&2
  fi
  echo "encryption: key from ENCRYPTION_SECRET"
else
  _omi_secret_dir="${STATE:-/data}/secrets"
  _omi_enc_file="$_omi_secret_dir/encryption_secret"
  if [ -s "$_omi_enc_file" ]; then
    ENCRYPTION_SECRET="$(cat "$_omi_enc_file")"
    echo "encryption: key from $_omi_enc_file"
  elif _omi_volume_used; then
    ENCRYPTION_SECRET="$_OMI_PUBLISHED_ENCRYPTION_SECRET"
    echo "WARNING: this data was written with the ENCRYPTION_SECRET published in the" >&2
    echo "  omi-local repo, so it is not protected by a private key. It is kept so the" >&2
    echo "  data stays readable. See SELF_HOSTING_ROADMAP.md (open item 0)." >&2
  else
    mkdir -p -m 700 "$_omi_secret_dir"
    # Owner-only before the first byte, and never a half-written file.
    (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$_omi_enc_file.tmp" \
      && mv "$_omi_enc_file.tmp" "$_omi_enc_file")
    ENCRYPTION_SECRET="$(cat "$_omi_enc_file" 2>/dev/null)"
    if [ -z "$ENCRYPTION_SECRET" ]; then
      echo "error: could not create $_omi_enc_file; set ENCRYPTION_SECRET" >&2
      exit 1
    fi
    echo "encryption: new key made in $_omi_enc_file (back it up with the data)"
  fi
fi
export ENCRYPTION_SECRET
