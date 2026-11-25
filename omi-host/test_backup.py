#!/usr/bin/env python3
"""backup/omi_backup.py: snapshot, checksums, restore safety, retention.

Runs the real tool against scratch data directories. What this pins down:

  * a snapshot holds the irreplaceable state only (export, Redis RDB, storage, Chroma,
    Radicale collections without its cache/lock, secrets) with a per-file sha256 manifest,
    and the outer .json carries the archive checksum, versions and commit,
  * a Chroma SQLite file in WAL mode is copied with its uncheckpointed writes,
  * .env is left out (with a warning) unless encryption is on and it is asked for,
  * restore checks every checksum before writing: a flipped byte, a wrong manifest or a
    path escaping the target is refused and nothing is written,
  * restore refuses a non-empty target without --force, and --force moves it aside,
  * the restored Redis directory gets an AOF seeded from the RDB (Redis 7 with
    appendonly yes otherwise starts EMPTY and ignores dump.rdb),
  * an age-encrypted snapshot round-trips (when age is installed here),
  * the flag quiesce waits for maintenance.sh's answer with the same id,
  * retention keeps the newest of each of N days, M weeks, K months, and prune deletes
    only the rest (plus day-old leftovers of interrupted runs).

Run:  python3 test_backup.py     (no pytest required)
"""

import contextlib
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backup"))
import omi_backup as ob  # noqa: E402

UTC = dt.timezone.utc
ROOTS = []


def mkroot():
    ROOTS.append(tempfile.mkdtemp(prefix="omi-backup-test-"))
    return ROOTS[-1]


@contextlib.contextmanager
def environ(**values):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update({k: v for k, v in values.items() if v is not None})
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def write(path, data, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(data)


def make_state(root):
    data = os.path.join(root, "data")
    write(os.path.join(data, "firebase-export", "firebase-export-metadata.json"), '{"version":"15.28.2"}')
    write(os.path.join(data, "firebase-export", "firestore_export", "all_namespaces", "output-0"), "docs")
    write(os.path.join(data, "firebase-export", "auth_export", "accounts.json"), '{"users":[{"localId":"a"},{"localId":"b"}]}')
    write(os.path.join(data, "redis", "dump.rdb"), "REDIS0011rest")
    write(os.path.join(data, "redis", "appendonlydir", "appendonly.aof.1.incr.aof"), "aof")
    write(os.path.join(data, "storage", "omi-memories-recordings", "u1", "c1.opus"), os.urandom(4096), "wb")
    write(os.path.join(data, "secrets", "local_auth_jwt_secret"), "s3cret")
    cal = os.path.join(data, "radicale", "collections", "collection-root", "omi", "tasks")
    write(os.path.join(cal, ".Radicale.props"), '{"tag":"VCALENDAR"}')
    write(os.path.join(cal, "t1.ics"), "BEGIN:VCALENDAR\nBEGIN:VTODO\nEND:VTODO\nEND:VCALENDAR\n")
    write(os.path.join(cal, "e1.ics"), "BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VEVENT\nEND:VCALENDAR\n")
    write(os.path.join(cal, ".Radicale.cache", "item", "t1.ics"), "cache")
    write(os.path.join(data, "radicale", "config"), "[server]")
    write(os.path.join(data, "hf-cache", "model.bin"), "x" * 100)
    write(os.path.join(data, "logs", "backend.log"), "log")
    os.makedirs(os.path.join(data, "chroma", "vector_db"))
    con = sqlite3.connect(os.path.join(data, "chroma", "vector_db", "chroma.sqlite3"))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE embeddings(id INTEGER)")
    con.execute("CREATE TABLE collections(id INTEGER)")
    con.executemany("INSERT INTO embeddings VALUES (?)", [(i,) for i in range(5)])
    con.execute("INSERT INTO collections VALUES (1)")
    con.commit()
    # Left open on purpose: the rows sit in the -wal file, not the main database file.
    return data, con


def settings_for(root, data, **extra):
    backups = os.path.join(root, "backups")
    os.makedirs(backups, exist_ok=True)
    values = {
        "OMI_BACKUP_DIR": backups,
        "OMI_BACKUP_DATA_DIR": data,
        "OMI_BACKUP_QUIESCE": "none",
        "OMI_BACKUP_CONFIG_DIR": os.path.join(root, "host"),
        "OMI_GIT_COMMIT": "0123456789abcdef",
        "OMI_BACKUP_ENCRYPT": None,
        "OMI_BACKUP_INCLUDE_ENV": None,
    }
    values.update(extra)
    with environ(**values):
        s = ob.Settings()
    write(os.path.join(root, "host", ".env"), "OPENAI_API_KEY=sk-test\n")
    return s


def members(archive):
    with tarfile.open(archive, "r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


def quiet(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        result = fn(*a, **kw)
    return result, out.getvalue()


def test_snapshot_contents_manifest_and_outer_json():
    root = mkroot()
    data, con = make_state(root)
    s = settings_for(root, data)
    summary, out = quiet(ob.backup, s)
    con.close()
    archive = os.path.join(s.backup_dir, summary["archive"])
    names = members(archive)
    assert "data/firebase-export/firebase-export-metadata.json" in names
    assert "data/redis/dump.rdb" in names
    assert not any("appendonlydir" in n for n in names), "a stack quiesced (or stopped) is restored from the RDB"
    assert "data/storage/omi-memories-recordings/u1/c1.opus" in names
    assert "data/secrets/local_auth_jwt_secret" in names
    assert "data/radicale/collections/collection-root/omi/tasks/t1.ics" in names
    assert not any(".Radicale.cache" in n for n in names), "Radicale's cache is rebuilt"
    assert "data/radicale/config" not in names, "regenerated on every start"
    assert not any(n.startswith(("data/hf-cache", "data/logs")) for n in names)
    assert not any(n.startswith("config/") for n in names), ".env needs encryption"
    assert ".env is not in this backup" in out, out

    with tarfile.open(archive, "r:gz") as tar:
        manifest = json.loads(tar.extractfile(ob.MANIFEST_NAME).read())
        for f in manifest["files"]:
            assert hashlib.sha256(tar.extractfile(f["path"]).read()).hexdigest() == f["sha256"], f["path"]
        chroma = tar.extractfile("data/chroma/vector_db/chroma.sqlite3").read()
    assert manifest["git_commit"] == "0123456789abcdef"
    assert manifest["versions"]["firebase_export"] == "15.28.2" and manifest["versions"]["redis_rdb"] == "0011"
    assert manifest["counts"]["calendar"] == {"calendars": 1, "events": 1, "tasks": 1, "items": 2}
    assert manifest["counts"]["auth_users"] == 2
    assert manifest["counts"]["vector_store"]["embeddings"] == 5
    # The WAL's rows made it into the copy.
    copy = os.path.join(root, "chroma-copy.sqlite3")
    write(copy, chroma, "wb")
    assert sqlite3.connect(copy).execute("SELECT count(*) FROM embeddings").fetchone()[0] == 5

    meta = json.load(open(os.path.join(s.backup_dir, summary["name"] + ".json")))
    assert meta["archive_sha256"] == ob.sha256_file(archive)
    assert meta["archive_bytes"] == os.path.getsize(archive)
    assert "files" not in meta, "the outer json stays small"
    assert not [n for n in os.listdir(s.backup_dir) if n.endswith(".partial") or n.startswith(".omi-backup-")]


def test_restore_checks_before_writing_and_refuses_damage():
    root = mkroot()
    data, con = make_state(root)
    s = settings_for(root, data)
    summary, _ = quiet(ob.backup, s)
    con.close()
    archive = os.path.join(s.backup_dir, summary["archive"])
    target = os.path.join(root, "restored")

    # 1. a flipped byte: the outer checksum catches it, nothing is written
    damaged = os.path.join(s.backup_dir, "omi-backup-20000101T000000Z.tar.gz")
    raw = bytearray(open(archive, "rb").read())
    raw[len(raw) // 2] ^= 0xFF
    write(damaged, bytes(raw), "wb")
    meta = json.load(open(os.path.join(s.backup_dir, summary["name"] + ".json")))
    write(damaged[: -len(".tar.gz")] + ".json", json.dumps(dict(meta, archive=os.path.basename(damaged))))
    try:
        quiet(ob.restore, damaged, target, s, skip_running_check=True)
        raise AssertionError("a damaged archive was restored")
    except ob.BackupError as e:
        assert "checksum mismatch" in str(e), e
    assert not os.path.exists(target)

    # 2. a member whose bytes differ from its manifest entry (no outer json to help)
    forged = os.path.join(root, "forged.tar.gz")
    with gzip.open(forged, "wb") as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        body = b"tampered"
        ti = tarfile.TarInfo("data/secrets/k")
        ti.size = len(body)
        tar.addfile(ti, io.BytesIO(body))
        m = json.dumps({"created_at": "x", "files": [{"path": "data/secrets/k", "sha256": "0" * 64, "size": 8}]}).encode()
        ti = tarfile.TarInfo(ob.MANIFEST_NAME)
        ti.size = len(m)
        tar.addfile(ti, io.BytesIO(m))
    try:
        quiet(ob.restore, forged, target, s, skip_running_check=True)
        raise AssertionError("a forged archive was restored")
    except ob.BackupError as e:
        assert "checksum check failed" in str(e), e
    assert not os.path.exists(target)

    # 3. a path escaping the target
    evil = os.path.join(root, "evil.tar.gz")
    with gzip.open(evil, "wb") as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        ti = tarfile.TarInfo("data/../../escape")
        ti.size = 1
        tar.addfile(ti, io.BytesIO(b"x"))
    try:
        quiet(ob.restore, evil, target, s, skip_running_check=True)
        raise AssertionError("a path traversal was accepted")
    except ob.BackupError as e:
        assert "unsafe path" in str(e), e
    assert not os.path.exists(os.path.join(root, "escape"))

    # 4. dry run on the good archive: checks pass, nothing written
    result, out = quiet(ob.restore, archive, target, s, dry_run=True, skip_running_check=True)
    assert result["dry_run"] and "all " in out and not os.path.exists(target), out


def test_restore_refuses_non_empty_without_force_and_moves_aside_with_it():
    root = mkroot()
    data, con = make_state(root)
    s = settings_for(root, data)
    summary, _ = quiet(ob.backup, s)
    con.close()
    archive = os.path.join(s.backup_dir, summary["archive"])

    target = os.path.join(root, "fresh")
    quiet(ob.restore, archive, target, s, skip_running_check=True)
    assert open(os.path.join(target, "secrets", "local_auth_jwt_secret")).read() == "s3cret"
    rec = os.path.join(target, "storage", "omi-memories-recordings", "u1", "c1.opus")
    assert open(rec, "rb").read() == open(os.path.join(data, "storage", "omi-memories-recordings", "u1", "c1.opus"), "rb").read()
    aof = os.path.join(target, "redis", "appendonlydir")
    assert open(os.path.join(aof, "appendonly.aof.manifest")).read() == "file appendonly.aof.1.base.rdb seq 1 type b\n"
    assert open(os.path.join(aof, "appendonly.aof.1.base.rdb")).read() == "REDIS0011rest"
    assert ob.count_radicale(os.path.join(target, "radicale", "collections"))["tasks"] == 1

    # The live data dir is not empty: refused.
    try:
        quiet(ob.restore, archive, data, s, skip_running_check=True)
        raise AssertionError("restored over live data without --force")
    except ob.BackupError as e:
        assert "--force" in str(e), e
    write(os.path.join(data, "secrets", "local_auth_jwt_secret"), "newer")
    _, out = quiet(ob.restore, archive, data, s, force=True, skip_running_check=True)
    assert open(os.path.join(data, "secrets", "local_auth_jwt_secret")).read() == "s3cret"
    aside = [n for n in os.listdir(data) if n.startswith(".pre-restore-")]
    assert len(aside) == 1, os.listdir(data)
    assert open(os.path.join(data, aside[0], "secrets", "local_auth_jwt_secret")).read() == "newer"
    assert os.path.isfile(os.path.join(data, "hf-cache", "model.bin")), "unmanaged dirs are left alone"
    assert not [n for n in os.listdir(data) if n.startswith(".restore-")], "staging removed"


def test_age_encrypted_snapshot_round_trips_with_env():
    if not (shutil.which("age") and shutil.which("age-keygen")):
        print("  (skipped: age not installed here)")
        return
    root = mkroot()
    data, con = make_state(root)
    key = os.path.join(root, "key.txt")
    subprocess.run(["age-keygen", "-o", key], check=True, capture_output=True)
    recipient = [l.split(": ")[1] for l in open(key) if l.startswith("# public key")][0].strip()
    s = settings_for(root, data, OMI_BACKUP_ENCRYPT="age", OMI_BACKUP_AGE_RECIPIENTS=recipient, OMI_BACKUP_INCLUDE_ENV="1")
    summary, _ = quiet(ob.backup, s)
    con.close()
    archive = os.path.join(s.backup_dir, summary["archive"])
    assert archive.endswith(".tar.gz.age")
    assert b"s3cret" not in open(archive, "rb").read()
    assert summary["counts"] == {}, "who is in an encrypted backup stays inside it"
    try:
        quiet(ob.restore, archive, os.path.join(root, "r0"), s, skip_running_check=True)
        raise AssertionError("decrypted without a key")
    except ob.BackupError as e:
        assert "identity" in str(e), e
    cfg = os.path.join(root, "cfg")
    quiet(ob.restore, archive, os.path.join(root, "r"), s, identity=key, config_dir=cfg, skip_running_check=True)
    assert open(os.path.join(cfg, ".env")).read() == "OPENAI_API_KEY=sk-test\n"
    assert open(os.path.join(root, "r", "secrets", "local_auth_jwt_secret")).read() == "s3cret"


def test_flag_quiesce_waits_for_the_matching_answer():
    root = mkroot()
    data = os.path.join(root, "data")
    os.makedirs(data)
    write(os.path.join(data, ob.SNAPSHOT_DONE), json.dumps({"id": "stale", "ok": True}))

    def answer():
        req = os.path.join(data, ob.SNAPSHOT_REQUEST)
        for _ in range(100):
            if os.path.exists(req):
                rid = open(req).read().strip()
                os.remove(req)
                write(os.path.join(data, ob.SNAPSHOT_DONE), json.dumps({"id": rid, "ok": True, "detail": "done"}))
                return
            time.sleep(0.05)

    t = threading.Thread(target=answer)
    t.start()
    result = ob._quiesce_flag(data, timeout=10)
    t.join()
    assert result == {"ok": True, "detail": "done"}, result

    result = ob._quiesce_flag(data, timeout=10, pickup_s=1)
    assert not result["ok"] and result["not_picked_up"] and "did not pick up" in result["detail"], result
    assert not os.path.exists(os.path.join(data, ob.SNAPSHOT_REQUEST)), "an unanswered request is withdrawn"


def T(s):
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def test_retention_keeps_days_weeks_months():
    # One backup a day at 03:30 for 400 days, ending Wed 2026-11-25.
    end = T("2026-11-25 03:30")
    times = [end - dt.timedelta(days=i) for i in range(400)]
    keep = ob.retention_keep(times, daily=7, weekly=4, monthly=6)
    days = {t.date() for t in keep}
    for i in range(7):
        assert (end - dt.timedelta(days=i)).date() in days
    # Weeks: newest of the current ISO week and the 3 before (Sundays of past weeks).
    for sunday in ("2026-11-22", "2026-11-15", "2026-11-08"):
        assert dt.date.fromisoformat(sunday) in days, sunday
    # Months: the newest of each of Nov (today), Oct..Jun -> last days of those months.
    for last in ("2026-10-31", "2026-09-30", "2026-08-31", "2026-07-31", "2026-06-30"):
        assert dt.date.fromisoformat(last) in days, last
    assert dt.date.fromisoformat("2026-05-31") not in days
    # 7 days (19th-25th, which already hold this week's and Sunday the 22nd), weeks add the
    # 15th and 8th, months add five month-ends (November's newest is today).
    assert len(keep) == 7 + 2 + 5, sorted(days)

    assert ob.retention_keep([], 7, 4, 6) == set()
    assert ob.retention_keep([end - dt.timedelta(days=30)], 0, 0, 0) == {end - dt.timedelta(days=30)}, "newest always kept"
    # Two a day: only the newest of each day counts as that day's.
    twice = [T("2026-11-25 03:30"), T("2026-11-25 15:00"), T("2026-11-24 03:30")]
    assert ob.retention_keep(twice, 7, 0, 0) == {T("2026-11-25 15:00"), T("2026-11-24 03:30")}


def test_prune_deletes_only_what_retention_drops():
    root = mkroot()
    backups = os.path.join(root, "b")
    os.makedirs(backups)
    end = T("2026-11-25 03:30")
    for i in range(20):
        name = "omi-backup-" + (end - dt.timedelta(days=i)).strftime("%Y%m%dT%H%M%SZ")
        write(os.path.join(backups, name + ".tar.gz"), "a")
        write(os.path.join(backups, name + ".json"), "{}")
    write(os.path.join(backups, "notes.txt"), "mine")
    write(os.path.join(backups, "omi-backup-20261125T040000Z.tar.gz.partial"), "x")
    old_partial = os.path.join(backups, "omi-backup-20261101T040000Z.tar.gz.partial")
    write(old_partial, "x")
    os.utime(old_partial, (time.time() - 2 * 86400,) * 2)
    with environ(OMI_BACKUP_DIR=backups, OMI_BACKUP_KEEP_DAILY="3", OMI_BACKUP_KEEP_WEEKLY="2", OMI_BACKUP_KEEP_MONTHLY="1"):
        s = ob.Settings()
    (would, _) = quiet(ob.prune, s, True)
    assert len(os.listdir(backups)) == 43, "dry run deletes nothing"
    removed, _ = quiet(ob.prune, s)
    assert sorted(removed) == sorted(would)
    left = sorted(b["name"] for b in ob.list_backups(backups))
    # 3 days (25, 24, 23), newest of last ISO week (Sun 22nd); month = 25th already.
    assert left == [
        "omi-backup-20261122T033000Z",
        "omi-backup-20261123T033000Z",
        "omi-backup-20261124T033000Z",
        "omi-backup-20261125T033000Z",
    ], left
    assert all(os.path.exists(os.path.join(backups, n + ".json")) for n in left)
    assert os.path.exists(os.path.join(backups, "notes.txt"))
    assert os.path.exists(os.path.join(backups, "omi-backup-20261125T040000Z.tar.gz.partial")), "may still be running"
    assert not os.path.exists(old_partial)


def main() -> None:
    tests = [
        test_snapshot_contents_manifest_and_outer_json,
        test_restore_checks_before_writing_and_refuses_damage,
        test_restore_refuses_non_empty_without_force_and_moves_aside_with_it,
        test_age_encrypted_snapshot_round_trips_with_env,
        test_flag_quiesce_waits_for_the_matching_answer,
        test_retention_keeps_days_weeks_months,
        test_prune_deletes_only_what_retention_drops,
    ]
    try:
        for t in tests:
            t()
            print(f"PASS: {t.__name__}")
    finally:
        for root in ROOTS:
            shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
