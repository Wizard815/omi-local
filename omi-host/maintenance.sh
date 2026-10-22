# Data durability for the omi-local container. Sourced by entrypoint.sh; also
# sourced directly by omi-host/test_maintenance.py.
#
# The Firestore emulator keeps every conversation in memory and writes them to
# disk only when it is asked to. Before this file, the only ask was
# --export-on-exit, so a crash, a power loss or a stop that outran the grace
# period lost everything since the last clean start. Now:
#
#   omi_export_firestore   asks the running emulator to export, into a scratch
#                          directory first, then swaps it in (the previous
#                          export stays as firebase-export.prev). Runs every
#                          OMI_FIRESTORE_EXPORT_INTERVAL_S (default 900; 0 = off).
#   omi_backup             a fresh export, a Redis snapshot, then one tarball of
#                          everything that cannot be re-downloaded, kept in
#                          $STATE/backups (OMI_BACKUP_KEEP newest, default 7).
#                          Runs every OMI_BACKUP_INTERVAL_S (default 86400; 0 = off).
#   omi_maintenance_loop   runs both on their schedules; entrypoint.sh starts it
#                          once the emulators are up.
#
# Restore: stop the stack, unpack a tarball over the data volume's root
# (`tar xzf omi-data-<stamp>.tar.gz -C /path/to/volume`), start the stack.

STATE="${STATE:-/data}"
FIREBASE_BIN="${FIREBASE_BIN:-firebase}"
REDIS_CLI="${REDIS_CLI:-redis-cli}"
OMI_FIREBASE_PROJECT="${OMI_FIREBASE_PROJECT:-demo-omi-local}"
OMI_FIRESTORE_EXPORT_INTERVAL_S="${OMI_FIRESTORE_EXPORT_INTERVAL_S:-900}"
OMI_BACKUP_INTERVAL_S="${OMI_BACKUP_INTERVAL_S:-86400}"
OMI_BACKUP_KEEP="${OMI_BACKUP_KEEP:-7}"

omi_log() { echo "maintenance: $*"; }

# Export the live emulator state into $STATE/firebase-export, atomically.
omi_export_firestore() {
  local target="$STATE/firebase-export" scratch="$STATE/firebase-export.tmp"
  rm -rf "$scratch"
  if ! "$FIREBASE_BIN" emulators:export "$scratch" --project "$OMI_FIREBASE_PROJECT" --force >/dev/null 2>&1 \
     || [ ! -s "$scratch/firebase-export-metadata.json" ]; then
    omi_log "firestore export FAILED; keeping the previous export" >&2
    rm -rf "$scratch"
    return 1
  fi
  rm -rf "$target.prev"
  if [ -d "$target" ]; then
    mv "$target" "$target.prev"
  fi
  mv "$scratch" "$target"
  omi_log "firestore exported to $target"
}

# Ask Redis for a point-in-time snapshot (dump.rdb next to the AOF) and wait for it.
omi_snapshot_redis() {
  local i
  "$REDIS_CLI" -p 6380 BGSAVE >/dev/null 2>&1 || return 0
  for i in $(seq 1 60); do
    case "$("$REDIS_CLI" -p 6380 INFO persistence 2>/dev/null | tr -d '\r' | grep '^rdb_bgsave_in_progress:')" in
      *:0) return 0 ;;
    esac
    sleep 1
  done
}

# One tarball of the data that cannot be re-downloaded, then prune old ones.
omi_backup() {
  local stamp dir="$STATE/backups" members=() name
  omi_export_firestore || return 1
  omi_snapshot_redis
  mkdir -p "$dir"
  for name in firebase-export redis storage chroma secrets; do
    [ -e "$STATE/$name" ] && members+=("$name")
  done
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  if ! tar czf "$dir/omi-data-$stamp.tar.gz.tmp" -C "$STATE" "${members[@]}" 2>/dev/null; then
    omi_log "backup FAILED" >&2
    rm -f "$dir/omi-data-$stamp.tar.gz.tmp"
    return 1
  fi
  mv "$dir/omi-data-$stamp.tar.gz.tmp" "$dir/omi-data-$stamp.tar.gz"
  omi_log "backup written: $dir/omi-data-$stamp.tar.gz"
  # Newest first; delete everything past OMI_BACKUP_KEEP.
  ls -1t "$dir"/omi-data-*.tar.gz 2>/dev/null | tail -n +"$((OMI_BACKUP_KEEP + 1))" | while IFS= read -r old; do
    rm -f "$old"
    omi_log "pruned old backup: $old"
  done
}

omi_maintenance_loop() {
  local next_export next_backup now
  now="$(date +%s)"
  next_export=$((now + OMI_FIRESTORE_EXPORT_INTERVAL_S))
  next_backup=$((now + OMI_BACKUP_INTERVAL_S))
  omi_log "timed export every ${OMI_FIRESTORE_EXPORT_INTERVAL_S}s, backup every ${OMI_BACKUP_INTERVAL_S}s (0 = off)"
  while sleep 30; do
    now="$(date +%s)"
    if [ "$OMI_BACKUP_INTERVAL_S" -gt 0 ] && [ "$now" -ge "$next_backup" ]; then
      omi_backup || true
      next_backup=$((now + OMI_BACKUP_INTERVAL_S))
      next_export=$((now + OMI_FIRESTORE_EXPORT_INTERVAL_S))
    elif [ "$OMI_FIRESTORE_EXPORT_INTERVAL_S" -gt 0 ] && [ "$now" -ge "$next_export" ]; then
      omi_export_firestore || true
      next_export=$((now + OMI_FIRESTORE_EXPORT_INTERVAL_S))
    fi
  done
}
