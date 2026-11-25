#!/usr/bin/env bash
# Restore a backup into the STOPPED Omi stack (BACKUP.md, "Restore").
#
#   bash omi-host/restore.sh --dry-run                 check the newest backup, say what would change
#   bash omi-host/restore.sh latest                    restore it (refuses if the volume holds data)
#   bash omi-host/restore.sh <archive or .json> --force   replace existing data (moved aside, not deleted)
#   bash omi-host/restore.sh latest --config-dir "$PWD"   also put a backed-up .env here (encrypted backups only)
#
# Every file's checksum is verified before anything is written. age-encrypted backups
# need OMI_BACKUP_AGE_IDENTITY (or --identity <key file>).
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/backup/common.sh"
dry=0
for a in "$@"; do [ "$a" = --dry-run ] && dry=1; done
if [ "$dry" = 0 ] && omi_container_running; then
  echo "error: ${OMI_BACKUP_CONTAINER:-omi-local} is running. Stop the stack first:" >&2
  echo "  cd $OMI_HOST_DIR && docker compose down     (the data volume stays)" >&2
  exit 2
fi
if [ "$(omi_backup_runner)" = docker ]; then
  # No omi-local to share a network with: run detached from it.
  vol="$(omi_data_volume)"
  [ -n "$vol" ] || { echo "error: no omi-data volume; create it with: docker volume create omi-host_omi-data" >&2; exit 2; }
  args=()
  for a in "$@"; do
    case "$a" in
      "$OMI_BACKUP_DIR"/*) args+=("/backups/${a#"$OMI_BACKUP_DIR"/}") ;;
      "$OMI_HOST_DIR") args+=(/omi-host) ;;
      *) args+=("$a") ;;
    esac
  done
  extra=()
  if [ -n "${OMI_BACKUP_AGE_IDENTITY:-}" ]; then
    extra+=(-v "$OMI_BACKUP_AGE_IDENTITY:/run/omi-age-identity:ro" -e OMI_BACKUP_AGE_IDENTITY=/run/omi-age-identity)
  fi
  exec docker run --rm -v "$vol:/data" -v "${OMI_BACKUP_DIR:?set OMI_BACKUP_DIR}:/backups:ro" -v "$OMI_HOST_DIR:/omi-host" \
    "${extra[@]}" -e OMI_BACKUP_DIR=/backups -e OMI_BACKUP_DATA_DIR=/data \
    --entrypoint "$OMI_BACKUP_PY_IN_IMAGE" "$OMI_BACKUP_IMAGE" /app/omi-host/backup/omi_backup.py restore "${args[@]}"
fi
omi_backup_run restore "$@"
