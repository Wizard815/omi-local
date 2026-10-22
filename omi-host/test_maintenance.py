#!/usr/bin/env python3
"""maintenance.sh: timed Firestore exports and tarball backups of the data volume.

Runs the real shell functions against a scratch STATE directory with a fake
`firebase` and `redis-cli` on PATH (the real CLI needs a running emulator).

What this pins down:

  * an export lands atomically: the live directory is swapped in only after
    the CLI wrote its metadata file, the previous export is kept as .prev,
    and a failed export leaves the current one untouched,
  * a backup is one tarball of firebase-export, redis, storage, chroma and
    secrets (whatever exists), model caches excluded, and only the newest
    OMI_BACKUP_KEEP tarballs survive,
  * a failed export fails the backup instead of archiving stale data.

Run:  python3 test_maintenance.py     (no pytest required)
"""

import os
import stat
import subprocess
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "maintenance.sh")

FAKE_FIREBASE = """#!/bin/bash
# emulators:export <dir> ...: behave like the CLI, or fail when asked to.
[ "$1" = emulators:export ] || exit 2
[ -n "$FAKE_FIREBASE_FAIL" ] && { echo "Error: export failed" >&2; exit 1; }
mkdir -p "$2/firestore_export" "$2/auth_export"
echo "{\\"version\\":\\"fake\\"}" > "$2/firebase-export-metadata.json"
echo "doc" > "$2/firestore_export/data"
"""
FAKE_REDIS = """#!/bin/bash
case "$*" in
  *BGSAVE*) echo "Background saving started" ;;
  *INFO*) printf 'rdb_bgsave_in_progress:0\\r\\n' ;;
esac
"""


def _bin(tmp, name, body):
    path = os.path.join(tmp, "bin", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def run(state, fn, **env):
    tmp = os.path.dirname(state)
    proc = subprocess.run(
        ["bash", "-c", f'set -u; . "$1"; {fn}', "_", SCRIPT],
        env={"PATH": os.path.join(tmp, "bin") + ":" + os.environ["PATH"], "STATE": state, **env},
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _state():
    tmp = tempfile.mkdtemp()
    state = os.path.join(tmp, "data")
    for d in ("firebase-export", "redis", "storage", "chroma", "secrets", "hf-cache", "whispercpp"):
        os.makedirs(os.path.join(state, d))
    with open(os.path.join(state, "firebase-export", "firebase-export-metadata.json"), "w") as f:
        f.write('{"version":"old"}')
    with open(os.path.join(state, "redis", "appendonly.aof"), "w") as f:
        f.write("aof")
    with open(os.path.join(state, "hf-cache", "big-model.bin"), "w") as f:
        f.write("x" * 100)
    _bin(tmp, "firebase", FAKE_FIREBASE)
    _bin(tmp, "redis-cli", FAKE_REDIS)
    return state


def test_export_swaps_in_atomically_and_keeps_the_previous_one():
    state = _state()
    code, out = run(state, "omi_export_firestore")
    assert code == 0, out
    assert open(os.path.join(state, "firebase-export", "firebase-export-metadata.json")).read().strip() == '{"version":"fake"}'
    assert open(os.path.join(state, "firebase-export.prev", "firebase-export-metadata.json")).read().strip() == '{"version":"old"}'
    assert not os.path.exists(os.path.join(state, "firebase-export.tmp"))


def test_a_failed_export_leaves_the_current_export_untouched():
    state = _state()
    code, out = run(state, "omi_export_firestore", FAKE_FIREBASE_FAIL="1")
    assert code == 1 and "FAILED" in out, out
    assert open(os.path.join(state, "firebase-export", "firebase-export-metadata.json")).read().strip() == '{"version":"old"}'
    assert not os.path.exists(os.path.join(state, "firebase-export.tmp"))
    assert not os.path.exists(os.path.join(state, "firebase-export.prev"))


def test_backup_archives_the_irreplaceable_data_and_prunes_old_ones():
    state = _state()
    backups = os.path.join(state, "backups")
    os.makedirs(backups)
    for i in range(3):
        with open(os.path.join(backups, f"omi-data-2026010{i}-000000.tar.gz"), "w") as f:
            f.write("old")
        os.utime(os.path.join(backups, f"omi-data-2026010{i}-000000.tar.gz"), (1 + i, 1 + i))
    time.sleep(0.01)

    code, out = run(state, "omi_backup", OMI_BACKUP_KEEP="2")
    assert code == 0, out
    names = sorted(os.listdir(backups))
    new = [n for n in names if n not in {"omi-data-20260100-000000.tar.gz", "omi-data-20260101-000000.tar.gz", "omi-data-20260102-000000.tar.gz"}]
    assert len(new) == 1 and not any(n.endswith(".tmp") for n in names), names
    # Newest two survive: the fresh one and the newest old one.
    assert names == sorted(["omi-data-20260102-000000.tar.gz", new[0]]), names
    with tarfile.open(os.path.join(backups, new[0])) as tar:
        members = {m.name for m in tar.getmembers()}
    assert "firebase-export/firebase-export-metadata.json" in members
    assert "redis/appendonly.aof" in members
    assert "chroma" in members and "storage" in members and "secrets" in members
    assert not any(m.startswith(("hf-cache", "whispercpp")) for m in members), "model caches are re-downloadable"
    assert "BGSAVE" not in out  # the fake redis answered; nothing to report


def test_backup_refuses_to_archive_a_stale_export():
    state = _state()
    code, out = run(state, "omi_backup", FAKE_FIREBASE_FAIL="1")
    assert code == 1, out
    assert not os.path.exists(os.path.join(state, "backups"))


def main() -> None:
    tests = [
        test_export_swaps_in_atomically_and_keeps_the_previous_one,
        test_a_failed_export_leaves_the_current_export_untouched,
        test_backup_archives_the_irreplaceable_data_and_prunes_old_ones,
        test_backup_refuses_to_archive_a_stale_export,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
