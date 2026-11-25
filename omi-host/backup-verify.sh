#!/usr/bin/env bash
# Test restore (BACKUP.md, "Test your backups"): unpack a backup into a scratch
# directory, start a throwaway Firestore emulator and Redis on it, read back the
# number of conversations, tasks, calendar items, vectors and stored files, and
# compare them with what the backup recorded. Nothing touches the live stack.
#
#   bash omi-host/backup-verify.sh             the newest backup
#   bash omi-host/backup-verify.sh <archive>   a given one
#   bash omi-host/backup-verify.sh --keep      leave the scratch copy for a look
#
# Exit 0 = OK. The report is printed and saved in $OMI_BACKUP_DIR/verify/.
# The omi-backup service runs this by itself on OMI_BACKUP_VERIFY_DAY each month.
# Needs room for one unpacked backup in OMI_BACKUP_VERIFY_TMP (default: OMI_BACKUP_DIR).
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/backup/common.sh"
if [ "$(omi_backup_runner)" = docker ]; then
  # The image has Java, firebase-tools and redis-server for the read-back.
  exec docker run --rm -v "${OMI_BACKUP_DIR:?set OMI_BACKUP_DIR}:/backups" \
    ${OMI_BACKUP_AGE_IDENTITY:+-v "$OMI_BACKUP_AGE_IDENTITY:/run/omi-age-identity:ro" -e OMI_BACKUP_AGE_IDENTITY=/run/omi-age-identity} \
    -e OMI_BACKUP_DIR=/backups --entrypoint "$OMI_BACKUP_PY_IN_IMAGE" "$OMI_BACKUP_IMAGE" \
    /app/omi-host/backup/omi_backup.py verify "$@"
fi
omi_backup_run verify "$@"
