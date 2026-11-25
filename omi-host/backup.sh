#!/usr/bin/env bash
# Take one backup of the Omi stack now, into OMI_BACKUP_DIR (BACKUP.md).
#
#   bash omi-host/backup.sh                 snapshot + retention (the stack may be running)
#   bash omi-host/backup.sh --no-prune      keep every old backup this time
#   bash omi-host/backup.sh --quiesce none  the stack is stopped: take the files as they are
#
# Runs on the host (cron / systemd timer); the omi-backup compose service does the same
# on its own schedule. Settings: OMI_BACKUP_* in .env (.env.example has them all).
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/backup/common.sh"
if [ "$(omi_backup_runner)" = docker ] && ! omi_container_running; then
  # The throwaway container shares omi-local's network to reach it; without it, files as they are.
  echo "omi-local is not running: backing up the files as they are" >&2
  exec docker run --rm -v "$(omi_data_volume):/data" -v "${OMI_BACKUP_DIR:?set OMI_BACKUP_DIR}:/backups" \
    -v "$OMI_HOST_DIR:/omi-host:ro" -e OMI_BACKUP_DIR=/backups -e OMI_BACKUP_DATA_DIR=/data \
    -e OMI_BACKUP_CONFIG_DIR=/omi-host -e OMI_GIT_COMMIT -e TZ \
    $(env | sed -n 's/^\(OMI_BACKUP_[A-Z_]*\)=.*/-e \1/p' | grep -v -e '_DIR$' -e 'AGE_IDENTITY' -e 'RECIPIENTS_FILE') \
    --entrypoint "$OMI_BACKUP_PY_IN_IMAGE" "$OMI_BACKUP_IMAGE" /app/omi-host/backup/omi_backup.py backup --quiesce none "$@"
fi
omi_backup_run backup "$@"
