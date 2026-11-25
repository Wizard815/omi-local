#!/usr/bin/env bash
# Backup demo: a stack full of real data is backed up, its disk "dies", the backup is
# restored onto an empty one, and the restored stack must show the same counts. Then a
# test restore of the same archive (backup-verify), and the health watchdog seeing Redis
# die and come back, with its alerts reaching a stand-in ntfy.
#
#   OMI_RADICALE_BIN=<radicale 3.5> omi-host/demo/backup_demo.sh          run, tear down
#   OMI_RADICALE_BIN=<radicale 3.5> omi-host/demo/backup_demo.sh --keep   leave the restored
#                                    stack, the watchdog and the fake ntfy running
#
# 1. seed    caldav_demo.sh (the CalDAV demo, ~4 min) fills stack A: three users, their
#            conversations, tasks, events and day summaries, synced to Radicale.
# 2. backup  backup/omi_backup.py with quiesce = maintenance.sh's own export + BGSAVE
#            (as `docker exec` runs it in the container); then the counts are recorded.
# 3. lose    stack A is killed and its state directory deleted.
# 4. restore --dry-run, then a restore into an empty directory B.
# 5. compare stack B starts from B (the emulators import the restored export, Redis loads
#            the snapshot, Radicale reads the calendars with the restored password) and
#            backup_demo.py logs in as the same user and compares every count.
# 6. verify  backup-verify's test restore of the same archive into a scratch directory.
# 7. alerts  health/watchdog.py watches stack B every 2 s; Redis is killed and restarted;
#            the down and recovered alerts must reach fake_ntfy.py.
#
# Needs what caldav_demo.sh needs (backend/.venv, `npm ci` at the root for firebase-tools,
# Java 21, redis-server, Radicale). Ports default away from caldav_demo.sh's own; each
# OMI_DEMO_*_PORT moves one.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
PY="${OMI_DEMO_PYTHON:-$REPO/backend/.venv/bin/python}"
export FIREBASE_BIN="${FIREBASE_BIN:-$REPO/node_modules/.bin/firebase}"
export OMI_DEMO_BACKEND_PORT="${OMI_DEMO_BACKEND_PORT:-8100}"
export OMI_DEMO_FIRESTORE_PORT="${OMI_DEMO_FIRESTORE_PORT:-8185}"
export OMI_DEMO_AUTH_PORT="${OMI_DEMO_AUTH_PORT:-9199}"
export OMI_DEMO_CALDAV_PORT="${OMI_DEMO_CALDAV_PORT:-5332}"
export OMI_DEMO_REDIS_PORT="${OMI_DEMO_REDIS_PORT:-6480}"
export OMI_DEMO_HUB_PORT="${OMI_DEMO_HUB_PORT:-4510}"
export OMI_DEMO_ALLGLANCE_PORT="${OMI_DEMO_ALLGLANCE_PORT:-8891}"
HEALTH_PORT="${OMI_DEMO_HEALTH_PORT:-8199}"
NTFY_PORT="${OMI_DEMO_NTFY_PORT:-8198}"
export OMI_BACKUP_DEMO_USER="${OMI_BACKUP_DEMO_USER:-backup-demo-alice}"
export OMI_BACKUP_DEMO_PASSWORD="${OMI_BACKUP_DEMO_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')}"
BACKUP_PY="$REPO/omi-host/backup/omi_backup.py"

WORK="${OMI_BACKUP_DEMO_WORK:-$(mktemp -d -t omi-backup-demo.XXXXXX)}"
A="$WORK/disk-a" B="$WORK/disk-b" NAS="$WORK/nas"
mkdir -p "$NAS"
echo "backup demo work dir: $WORK"
BG=()
cleanup() {
  local code=$?
  if [ "$KEEP" = 1 ] && [ "$code" = 0 ]; then
    echo "--keep: left running (pids ${BG[*]}); status page http://127.0.0.1:$HEALTH_PORT/"
    echo "stop with: kill ${BG[*]}; rm -rf $WORK"
    exit 0
  fi
  for pid in "${BG[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${BG[@]}"; do wait "$pid" 2>/dev/null || true; done
  [ -z "${OMI_BACKUP_DEMO_WORK:-}" ] && rm -rf "$WORK"
  exit "$code"
}
trap cleanup EXIT INT TERM
step() { printf '\n==== %s\n' "$*"; }
measure_args() {
  echo --backend "http://127.0.0.1:$OMI_DEMO_BACKEND_PORT" --caldav "http://127.0.0.1:$OMI_DEMO_CALDAV_PORT/" \
    --caldav-user omi --caldav-password "$(cat "$1/secrets/caldav_password")"
}

step "1. seed stack A with the CalDAV demo (users, conversations, tasks, events)"
set +e
OMI_DEMO_STATE="$A" "$HERE/caldav_demo.sh" --keep --omi-username "$OMI_BACKUP_DEMO_USER" \
  --omi-password "$OMI_BACKUP_DEMO_PASSWORD" 2>&1 | tee "$WORK/seed.log" | grep -E "^(PASS|FAIL|GAP)|passed|stop it with"
set -e
read -r -a PIDS_A <<< "$(sed -n 's/^stop it with: kill //p' "$WORK/seed.log")"
[ "${#PIDS_A[@]}" -gt 0 ] || { echo "stack A did not come up; see $WORK/seed.log" >&2; exit 1; }
BG+=("${PIDS_A[@]}")

step "2. back up stack A while it runs (quiesce: maintenance.sh's export + BGSAVE)"
OMI_BACKUP_DIR="$NAS" OMI_BACKUP_DATA_DIR="$A" OMI_BACKUP_QUIESCE=cmd \
OMI_BACKUP_QUIESCE_CMD="STATE='$A' OMI_REDIS_PORT=$OMI_DEMO_REDIS_PORT FIREBASE_BIN='$FIREBASE_BIN'; . '$REPO/omi-host/maintenance.sh' && omi_export_firestore && omi_snapshot_redis" \
OMI_BACKUP_FIRESTORE="127.0.0.1:$OMI_DEMO_FIRESTORE_PORT" \
  python3 "$BACKUP_PY" backup
# shellcheck disable=SC2046
OMI_BACKUP_DEMO_STATE="$A" "$PY" "$HERE/backup_demo.py" --phase measure --out "$WORK/before.json" $(measure_args "$A") >/dev/null
echo "counts of stack A recorded in $WORK/before.json"

step "3. the disk dies: stack A stopped, its data deleted"
for pid in "${PIDS_A[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
for pid in "${PIDS_A[@]}"; do while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done; done
BG=()
rm -rf "$A"
echo "deleted $A"

step "4. restore onto an empty disk (dry run first)"
OMI_BACKUP_DIR="$NAS" python3 "$BACKUP_PY" restore latest --data-dir "$B" --dry-run
OMI_BACKUP_DIR="$NAS" python3 "$BACKUP_PY" restore latest --data-dir "$B"

step "5. start stack B from the restored data and compare"
set +e
OMI_DEMO_STATE="$B" OMI_DEMO_FIRESTORE_IMPORT="$B/firebase-export" OMI_DEMO_DRIVER=backup_demo.py \
OMI_BACKUP_DEMO_PHASE=compare OMI_BACKUP_DEMO_EXPECT="$WORK/before.json" OMI_BACKUP_DEMO_OUT="$WORK/after.json" \
OMI_BACKUP_DEMO_STATE="$B" "$HERE/caldav_demo.sh" --keep 2>&1 | tee "$WORK/compare.log" | grep -v "^stop it with\|^--keep"
set -e
read -r -a PIDS_B <<< "$(sed -n 's/^stop it with: kill //p' "$WORK/compare.log")"
BG+=("${PIDS_B[@]}")
grep -q " 0 different" "$WORK/compare.log" || { echo "restored stack differs" >&2; exit 1; }

step "6. test restore of the same archive (backup-verify)"
OMI_BACKUP_DIR="$NAS" python3 "$BACKUP_PY" verify latest

step "7. watchdog: Redis dies and comes back; alerts reach a stand-in ntfy"
python3 "$HERE/fake_ntfy.py" --port "$NTFY_PORT" --log "$WORK/ntfy.jsonl" > "$WORK/ntfy.log" 2>&1 &
BG+=($!)
OMI_HEALTH_PORT="$HEALTH_PORT" OMI_HEALTH_BIND=127.0.0.1 OMI_HEALTH_NAME=omi-demo OMI_HEALTH_INTERVAL_S=2 \
OMI_HEALTH_FAIL_AFTER=2 OMI_HEALTH_COOLDOWN_S=60 OMI_HEALTH_PUBLIC_URL="http://127.0.0.1:$HEALTH_PORT/" \
OMI_HEALTH_BACKEND_URL="http://127.0.0.1:$OMI_DEMO_BACKEND_PORT" \
OMI_HEALTH_FIRESTORE_URL="http://127.0.0.1:$OMI_DEMO_FIRESTORE_PORT" \
OMI_HEALTH_REDIS="127.0.0.1:$OMI_DEMO_REDIS_PORT" OMI_HEALTH_CALDAV_URL="http://127.0.0.1:$OMI_DEMO_CALDAV_PORT" \
OMI_HEALTH_CALDAV_PASSWORD_FILE="$B/secrets/caldav_password" \
OMI_HEALTH_AUDIO_URL=off OMI_HEALTH_LLM_URL=off OMI_HEALTH_LLM_ADAPTER_URL=off HERMES_API_URL= \
OMI_HEALTH_DISKS="data=$B,backups=$NAS" OMI_HEALTH_BACKUP_DIR="$NAS" \
OMI_HEALTH_DISK_WARN_PCT="${OMI_HEALTH_DISK_WARN_PCT:-15}" OMI_HEALTH_DISK_CRIT_PCT="${OMI_HEALTH_DISK_CRIT_PCT:-5}" \
OMI_HEALTH_NTFY_URL="http://127.0.0.1:$NTFY_PORT" OMI_HEALTH_NTFY_TOPIC=omi-alerts \
  python3 "$REPO/omi-host/health/watchdog.py" > "$WORK/watchdog.log" 2>&1 &
BG+=($!)
wait_for() {  # wait_for <seconds> <command...>
  local n=$1
  shift
  for _ in $(seq 1 "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}
check_state() {  # check_state <check name>: its state on the status page, or nothing yet
  curl -sf "http://127.0.0.1:$HEALTH_PORT/status.json" 2>/dev/null | python3 -c '
import json, sys
print({c["name"]: c["state"] for c in json.load(sys.stdin)["checks"]}[sys.argv[1]])' "$1" 2>/dev/null
}
redis_ok() { [ "$(check_state redis)" = ok ]; }
wait_for 30 redis_ok || { echo "watchdog never saw redis ok; see $WORK/watchdog.log" >&2; exit 1; }
echo "watchdog up: http://127.0.0.1:$HEALTH_PORT/  redis=$(check_state redis)"
REDIS_PID="$(pgrep -f "redis-server 127.0.0.1:$OMI_DEMO_REDIS_PORT" | head -n 1)"
kill -TERM "$REDIS_PID"
echo "killed redis (pid $REDIS_PID)"
wait_for 30 grep -q "Redis DOWN" "$WORK/ntfy.jsonl" || { echo "no down alert" >&2; exit 1; }
redis-server --bind 127.0.0.1 --port "$OMI_DEMO_REDIS_PORT" --dir "$B/redis" --save "" --appendonly no \
  > "$WORK/redis-restarted.log" 2>&1 &
BG+=($!)
echo "restarted redis"
wait_for 30 grep -q "Redis recovered" "$WORK/ntfy.jsonl" || { echo "no recovered alert" >&2; exit 1; }
sleep 3
echo "alerts the stand-in ntfy received:"
python3 - "$WORK/ntfy.jsonl" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    e = json.loads(line)
    print(f"  {e['at']}  {e['path']}  priority={e['priority']} tags={e['tags']}  {e['title']}: {e['body']}")
PY
echo
echo "backup demo: OK"
