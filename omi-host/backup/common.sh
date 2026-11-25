# Shared by omi-host/backup.sh, restore.sh and backup-verify.sh (run on the HOST).
# Loads .env like run-omi-local-host.sh, finds the omi-data volume and runs
# backup/omi_backup.py either with the host's python3 or, where the host has none
# (stock Unraid), inside a throwaway omi-local container (OMI_BACKUP_RUNNER=docker).

OMI_HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$OMI_HOST_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$OMI_HOST_DIR/.env"
  set +a
fi
export OMI_BACKUP_CONFIG_DIR="${OMI_BACKUP_CONFIG_DIR:-$OMI_HOST_DIR}"
OMI_GIT_COMMIT="${OMI_GIT_COMMIT:-$(git -C "$OMI_HOST_DIR" rev-parse HEAD 2>/dev/null || true)}"
export OMI_GIT_COMMIT
OMI_BACKUP_IMAGE="${OMI_BACKUP_IMAGE:-omi-local:latest}"
OMI_BACKUP_PY_IN_IMAGE=/app/backend/.venv/bin/python

# The compose volume holding /data (named <project>_omi-data).
omi_data_volume() {
  docker volume ls -q --filter label=com.docker.compose.volume=omi-data 2>/dev/null | head -n 1
}

omi_backup_runner() {
  case "${OMI_BACKUP_RUNNER:-auto}" in
    host | docker) echo "$OMI_BACKUP_RUNNER" ;;
    *)
      if command -v python3 >/dev/null 2>&1 && { [ -n "${OMI_BACKUP_DATA_DIR:-}" ] || [ "$(id -u)" = 0 ]; }; then
        echo host
      elif command -v docker >/dev/null 2>&1; then
        echo docker
      else
        echo host
      fi
      ;;
  esac
}

omi_container_running() {
  command -v docker >/dev/null 2>&1 \
    && [ "$(docker inspect -f '{{.State.Running}}' "${OMI_BACKUP_CONTAINER:-omi-local}" 2>/dev/null)" = true ]
}

# omi_backup_run <subcommand> [args...]
omi_backup_run() {
  local runner vol
  runner="$(omi_backup_runner)"
  if [ "$runner" = host ]; then
    if [ -z "${OMI_BACKUP_DATA_DIR:-}" ]; then
      vol="$(omi_data_volume)"
      [ -n "$vol" ] || { echo "error: no omi-data volume found; set OMI_BACKUP_DATA_DIR" >&2; return 2; }
      OMI_BACKUP_DATA_DIR="$(docker volume inspect -f '{{.Mountpoint}}' "$vol")"
      export OMI_BACKUP_DATA_DIR
    fi
    python3 "$OMI_HOST_DIR/backup/omi_backup.py" "$@"
    return
  fi
  # docker runner: the same script inside the omi-local image, the volume mounted at /data.
  vol="$(omi_data_volume)"
  [ -n "$vol" ] || { echo "error: no omi-data volume found (has the stack ever started?)" >&2; return 2; }
  [ -n "${OMI_BACKUP_DIR:-}" ] || { echo "error: set OMI_BACKUP_DIR (e.g. your NAS mount) in .env" >&2; return 2; }
  local args=() a envs=() name
  for a in "$@"; do
    case "$a" in
      "$OMI_BACKUP_DIR"/*) args+=("/backups/${a#"$OMI_BACKUP_DIR"/}") ;;
      "$OMI_HOST_DIR") args+=("/omi-host") ;;
      *) args+=("$a") ;;
    esac
  done
  for name in $(env | sed -n 's/^\(OMI_BACKUP_[A-Z_]*\)=.*/\1/p'); do envs+=(-e "$name"); done
  local mounts=(-v "$vol:/data" -v "$OMI_BACKUP_DIR:/backups" -v "$OMI_HOST_DIR:/omi-host")
  if [ -n "${OMI_BACKUP_AGE_IDENTITY:-}" ]; then
    mounts+=(-v "$OMI_BACKUP_AGE_IDENTITY:/run/omi-age-identity:ro")
    envs+=(-e OMI_BACKUP_AGE_IDENTITY=/run/omi-age-identity)
  fi
  if [ -n "${OMI_BACKUP_AGE_RECIPIENTS_FILE:-}" ]; then
    mounts+=(-v "$OMI_BACKUP_AGE_RECIPIENTS_FILE:/run/omi-age-recipients:ro")
    envs+=(-e OMI_BACKUP_AGE_RECIPIENTS_FILE=/run/omi-age-recipients)
  fi
  docker run --rm --network "container:${OMI_BACKUP_CONTAINER:-omi-local}" "${mounts[@]}" "${envs[@]}" \
    -e OMI_BACKUP_DIR=/backups -e OMI_BACKUP_DATA_DIR=/data -e OMI_BACKUP_CONFIG_DIR=/omi-host \
    -e OMI_BACKUP_SIDECAR=1 -e OMI_GIT_COMMIT -e TZ \
    --entrypoint "$OMI_BACKUP_PY_IN_IMAGE" "$OMI_BACKUP_IMAGE" /app/omi-host/backup/omi_backup.py "${args[@]}"
}
