#!/usr/bin/env python3
"""Backups of a self-hosted Omi stack: snapshot, restore, test restore, retention.

Standard library only, so the same file runs on the host, in the omi-backup
sidecar (the omi-local image) and in the stdlib test lane. BACKUP.md is the
operator's guide; this header is the reference.

  omi_backup.py backup     one consistent snapshot of the data volume into OMI_BACKUP_DIR
  omi_backup.py restore    an archive into a STOPPED stack's data directory
  omi_backup.py verify     a test restore into a scratch directory, then counts read back
  omi_backup.py prune      apply the daily/weekly/monthly retention
  omi_backup.py list       the backups in OMI_BACKUP_DIR, newest first
  omi_backup.py counts     conversations, tasks, calendar items ... of a data dir or live stack
  omi_backup.py schedule   the sidecar's loop: backup daily at OMI_BACKUP_TIME, verify monthly

What a snapshot holds (paths inside the data volume, /data in the containers):

  firebase-export/          Firestore + Auth emulator export, made fresh first (quiesce)
  redis/dump.rdb            a BGSAVE taken right after it (restored as the AOF base too)
  storage/                  recordings, speech profiles, chat files (local blob storage)
  chroma/                   the vector store; SQLite files copied with SQLite's backup API
  radicale/collections/     calendars and task lists, read under Radicale's own shared lock
  secrets/                  login-signing key, CalDAV password, storage link key
  config/.env, config/docker-compose.override.yml
                            only with OMI_BACKUP_INCLUDE_ENV=1 AND encryption on (.env holds keys)

Left out on purpose (re-downloadable or regenerated): hf-cache, whispercpp, piper,
miopen, logs, backups, firebase-export.prev, radicale/config and radicale/users.

Quiesce (OMI_BACKUP_QUIESCE): the Firestore emulator keeps its data in memory, so a
snapshot first asks it to export, and asks Redis for a BGSAVE:
  docker  docker exec omi-local ... maintenance.sh's omi_export_firestore + omi_snapshot_redis
  flag    write $DATA/.snapshot-request; maintenance.sh's loop does the same and answers in
          $DATA/.snapshot-done (the sidecar uses this: no Docker socket needed)
  cmd     run OMI_BACKUP_QUIESCE_CMD (bash)
  none    take the files as they are (a stopped stack)
  auto    flag in the sidecar, or on a host where omi-local runs (falling back to docker
          when nobody picks the request up), else none
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid

FORMAT = 1
NAME_RE = re.compile(r"^omi-backup-(\d{8}T\d{6}Z)$")
ARCHIVE_SUFFIXES = (".tar.gz", ".tar.gz.age", ".tar.gz.gpg")
MANIFEST_NAME = "MANIFEST.json"
PROJECT = "demo-omi-local"
# Collection groups counted in Firestore (subcollections of users/{uid} mostly).
FIRESTORE_GROUPS = (
    "users",
    "conversations",
    "action_items",
    "memories",
    "memory_items",
    "day_reviews",
    "messages",
    "people",
)
RADICALE_SKIP = (".Radicale.cache", ".Radicale.lock", ".Radicale.tmp-*")
SNAPSHOT_REQUEST = ".snapshot-request"
SNAPSHOT_DONE = ".snapshot-done"


def log(msg: str) -> None:
    print(f"omi-backup: {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"omi-backup: WARNING: {msg}", file=sys.stderr, flush=True)


class BackupError(Exception):
    pass


# ----------------------------------------------------------------------------- settings


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value.strip() if value.strip() else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        raise BackupError(f"{name} must be a whole number")


class Settings:
    def __init__(self) -> None:
        self.data_dir = env("OMI_BACKUP_DATA_DIR", "/data")
        self.backup_dir = env("OMI_BACKUP_DIR")
        self.config_dir = env("OMI_BACKUP_CONFIG_DIR")
        self.keep_daily = env_int("OMI_BACKUP_KEEP_DAILY", 7)
        self.keep_weekly = env_int("OMI_BACKUP_KEEP_WEEKLY", 4)
        self.keep_monthly = env_int("OMI_BACKUP_KEEP_MONTHLY", 6)
        self.encrypt = env("OMI_BACKUP_ENCRYPT", "none").lower()
        self.age_recipients = [r.strip() for r in env("OMI_BACKUP_AGE_RECIPIENTS").split(",") if r.strip()]
        self.age_recipients_file = env("OMI_BACKUP_AGE_RECIPIENTS_FILE")
        self.age_identity = env("OMI_BACKUP_AGE_IDENTITY")
        self.gpg_recipient = env("OMI_BACKUP_GPG_RECIPIENT")
        self.include_env = env("OMI_BACKUP_INCLUDE_ENV", "0") == "1"
        self.gzip_level = env_int("OMI_BACKUP_GZIP_LEVEL", 6)
        self.quiesce = env("OMI_BACKUP_QUIESCE", "auto").lower()
        self.quiesce_cmd = env("OMI_BACKUP_QUIESCE_CMD")
        self.quiesce_timeout = env_int("OMI_BACKUP_QUIESCE_TIMEOUT_S", 600)
        self.container = env("OMI_BACKUP_CONTAINER", "omi-local")
        self.firestore = env("OMI_BACKUP_FIRESTORE", "")  # host:port of the live emulator, for counts
        self.offsite = env("OMI_BACKUP_OFFSITE", "").lower()
        self.restic_repo = env("OMI_BACKUP_RESTIC_REPOSITORY")
        self.rclone_remote = env("OMI_BACKUP_RCLONE_REMOTE")
        self.time = env("OMI_BACKUP_TIME", "03:30")
        self.verify_day = env_int("OMI_BACKUP_VERIFY_DAY", 1)
        self.verify_tmp = env("OMI_BACKUP_VERIFY_TMP")
        self.git_commit = env("OMI_GIT_COMMIT")

    def require_backup_dir(self) -> str:
        if not self.backup_dir:
            raise BackupError("OMI_BACKUP_DIR is not set (a directory, e.g. your NAS mount)")
        if not os.path.isdir(self.backup_dir):
            raise BackupError(f"OMI_BACKUP_DIR {self.backup_dir} does not exist or is not mounted")
        return self.backup_dir


# ----------------------------------------------------------------------------- helpers


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(name: str) -> dt.datetime | None:
    m = NAME_RE.match(name)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def git_commit(settings: Settings) -> str:
    if settings.git_commit:
        return settings.git_commit
    here = os.path.dirname(os.path.abspath(__file__))
    for cwd in (settings.config_dir, here):
        if not cwd:
            continue
        try:
            out = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return "unknown"


def fsync_dir(path: str) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_json_atomic(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(os.path.dirname(path) or ".")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        time.sleep(0.5)
    return False


# ----------------------------------------------------------------------------- redis (RESP)


def redis_command(host: str, port: int, *args: str, password: str = "", timeout: float = 5.0):
    """One Redis command over a raw socket; returns the decoded reply."""

    def encode(parts):
        out = f"*{len(parts)}\r\n".encode()
        for p in parts:
            b = p.encode()
            out += f"${len(b)}\r\n".encode() + b + b"\r\n"
        return out

    with socket.create_connection((host, port), timeout=timeout) as s:
        f = s.makefile("rb")
        if password:
            s.sendall(encode(["AUTH", password]))
            _read_resp(f)
        s.sendall(encode(list(args)))
        return _read_resp(f)


def _read_resp(f):
    line = f.readline()
    if not line:
        raise ConnectionError("redis closed the connection")
    kind, rest = line[:1], line[1:].rstrip(b"\r\n")
    if kind == b"+":
        return rest.decode()
    if kind == b"-":
        raise RuntimeError(rest.decode())
    if kind == b":":
        return int(rest)
    if kind == b"$":
        n = int(rest)
        if n < 0:
            return None
        data = f.read(n + 2)[:-2]
        return data.decode("utf-8", "replace")
    if kind == b"*":
        return [_read_resp(f) for _ in range(int(rest))]
    raise RuntimeError(f"unexpected redis reply {line!r}")


# ----------------------------------------------------------------------------- quiesce


def container_running(name: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


QUIESCE_SHELL = ". /app/omi-host/maintenance.sh && omi_export_firestore && omi_snapshot_redis"


def quiesce(settings: Settings, data_dir: str) -> dict:
    method = settings.quiesce
    fallback_docker = False
    if method == "auto":
        if os.environ.get("OMI_BACKUP_SIDECAR") == "1":
            method = "flag"
        elif container_running(settings.container):
            # The flag goes through maintenance.sh's loop, so it never races the loop's own
            # timed export; docker exec only for an image that predates the flag.
            method, fallback_docker = "flag", True
        else:
            method = "none"
    started = time.time()
    result = {"method": method, "ok": True, "detail": ""}
    if method == "none":
        result["detail"] = "files taken as they are (stack stopped, or nothing to flush)"
        return result
    if method in ("docker", "cmd"):
        if method == "docker":
            cmd = ["docker", "exec", settings.container, "bash", "-c", QUIESCE_SHELL]
        else:
            if not settings.quiesce_cmd:
                raise BackupError("OMI_BACKUP_QUIESCE=cmd needs OMI_BACKUP_QUIESCE_CMD")
            cmd = ["bash", "-c", settings.quiesce_cmd]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.quiesce_timeout)
            ok = out.returncode == 0
            detail = (out.stdout + out.stderr).strip().splitlines()
            result.update(ok=ok, detail=" / ".join(detail[-3:]))
        except subprocess.TimeoutExpired:
            result.update(ok=False, detail=f"timed out after {settings.quiesce_timeout}s")
    elif method == "flag":
        result.update(_quiesce_flag(data_dir, settings.quiesce_timeout))
        if not result["ok"] and result.pop("not_picked_up", False) and fallback_docker:
            return quiesce_with(settings, data_dir, "docker")
        result.pop("not_picked_up", None)
    else:
        raise BackupError(f"unknown OMI_BACKUP_QUIESCE={method}")
    result["seconds"] = round(time.time() - started, 1)
    return result


def quiesce_with(settings: Settings, data_dir: str, method: str) -> dict:
    forced = Settings()
    forced.__dict__.update(settings.__dict__)
    forced.quiesce = method
    return quiesce(forced, data_dir)


def _quiesce_flag(data_dir: str, timeout: int, pickup_s: float = 30) -> dict:
    request_id = uuid.uuid4().hex
    req, done = os.path.join(data_dir, SNAPSHOT_REQUEST), os.path.join(data_dir, SNAPSHOT_DONE)
    with open(req + ".tmp", "w") as f:
        f.write(request_id + "\n")
    os.replace(req + ".tmp", req)
    started = time.time()
    deadline = started + timeout
    while time.time() < deadline:
        with contextlib.suppress(OSError, ValueError):
            with open(done) as f:
                answer = json.load(f)
            if answer.get("id") == request_id:
                return {"ok": bool(answer.get("ok")), "detail": answer.get("detail", "")}
        if os.path.exists(req) and time.time() - started > pickup_s:
            # The loop polls every few seconds; nobody took the request.
            os.remove(req)
            return {
                "ok": False,
                "not_picked_up": True,
                "detail": f"omi-local did not pick up the request in {pickup_s:.0f}s (not running, or an image older than this tool)",
            }
        time.sleep(1)
    with contextlib.suppress(OSError):
        os.remove(req)
    return {"ok": False, "detail": f"omi-local took the request but did not answer in {timeout}s"}


# ----------------------------------------------------------------------------- snapshot sources


class HashingReader:
    """Feeds tarfile exactly `size` bytes, hashing them; pads if the file shrank."""

    def __init__(self, f, size: int):
        self.f, self.left, self.h, self.short = f, size, hashlib.sha256(), False

    def read(self, n: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        if n < 0 or n > self.left:
            n = self.left
        data = self.f.read(n)
        if len(data) < n:
            self.short = True
            data += b"\0" * (n - len(data))
        self.left -= len(data)
        self.h.update(data)
        return data


def _skip(name: str, patterns) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def walk_files(root: str, skip=()):
    """(abs path, rel path) of every regular file and dir under root, sorted, skipping patterns."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip(d, skip))
        rel_dir = os.path.relpath(dirpath, root)
        yield dirpath, ("" if rel_dir == "." else rel_dir), True
        for name in sorted(filenames):
            if _skip(name, skip):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            yield full, os.path.normpath(os.path.join(rel_dir, name)), False


@contextlib.contextmanager
def radicale_shared_lock(collections: str, timeout: float = 120):
    """Radicale's own storage lock, shared: its writers wait while we read."""
    import fcntl

    lock_path = os.path.join(collections, ".Radicale.lock")
    if not os.path.isdir(collections):
        yield False
        return
    f = open(lock_path, "a+")
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise BackupError("Radicale held its storage lock for too long")
                time.sleep(0.2)
        yield True
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def sqlite_snapshot(src: str, dst: str) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


# ----------------------------------------------------------------------------- counts


def count_radicale(collections: str) -> dict:
    out = {"calendars": 0, "events": 0, "tasks": 0, "items": 0}
    if not os.path.isdir(collections):
        return out
    for dirpath, dirnames, filenames in os.walk(collections):
        dirnames[:] = [d for d in dirnames if not _skip(d, RADICALE_SKIP)]
        if ".Radicale.props" in filenames:
            try:
                with open(os.path.join(dirpath, ".Radicale.props")) as f:
                    props = json.load(f)
                if props.get("tag") == "VCALENDAR":
                    out["calendars"] += 1
            except (OSError, ValueError):
                pass
        for name in filenames:
            if not name.endswith(".ics"):
                continue
            out["items"] += 1
            try:
                with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            out["events"] += text.count("BEGIN:VEVENT")
            out["tasks"] += text.count("BEGIN:VTODO")
    return out


def count_chroma(chroma_dir: str) -> dict:
    out = {"sqlite_files": 0, "embeddings": 0, "collections": 0}
    if not os.path.isdir(chroma_dir):
        return out
    for full, rel, is_dir in walk_files(chroma_dir):
        if is_dir or not is_sqlite(full):
            continue
        out["sqlite_files"] += 1
        with contextlib.suppress(sqlite3.Error):
            con = sqlite3.connect(f"file:{full}?mode=ro", uri=True)
            try:
                for table, key in (("embeddings", "embeddings"), ("collections", "collections")):
                    with contextlib.suppress(sqlite3.Error):
                        out[key] += con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            finally:
                con.close()
    return out


def count_files(root: str) -> dict:
    files = size = 0
    if os.path.isdir(root):
        for full, _rel, is_dir in walk_files(root):
            if not is_dir:
                files += 1
                size += os.path.getsize(full)
    return {"files": files, "bytes": size}


def count_auth_export(export_dir: str) -> int | None:
    path = os.path.join(export_dir, "auth_export", "accounts.json")
    try:
        with open(path) as f:
            return len(json.load(f).get("users") or [])
    except (OSError, ValueError):
        return None


def firestore_counts(host: str, groups=FIRESTORE_GROUPS, timeout: float = 30) -> dict:
    """COUNT(*) of each collection group on a running emulator, as its owner."""
    base = f"http://{host}/v1/projects/{PROJECT}/databases/(default)/documents:runAggregationQuery"
    out = {}
    for group in groups:
        body = {
            "structuredAggregationQuery": {
                "structuredQuery": {"from": [{"collectionId": group, "allDescendants": True}]},
                "aggregations": [{"alias": "n", "count": {}}],
            }
        }
        req = urllib.request.Request(
            base,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer owner"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rows = json.loads(resp.read())
        value = 0
        for row in rows:
            fields = (row.get("result") or {}).get("aggregateFields") or {}
            if "n" in fields:
                value = int(fields["n"].get("integerValue", 0))
        out[group] = value
    return out


def auth_count_live(host: str, timeout: float = 15) -> int:
    url = f"http://{host}/identitytoolkit.googleapis.com/v1/projects/{PROJECT}/accounts:query"
    req = urllib.request.Request(
        url,
        data=json.dumps({"returnUserInfo": False}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer owner"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(json.loads(resp.read()).get("recordsCount", 0))


def offline_counts(data_dir: str) -> dict:
    """What can be counted from files alone (no services)."""
    return {
        "calendar": count_radicale(os.path.join(data_dir, "radicale", "collections")),
        "vector_store": count_chroma(os.path.join(data_dir, "chroma")),
        "storage": count_files(os.path.join(data_dir, "storage")),
    }


@contextlib.contextmanager
def throwaway_firestore(export_dir: str, workdir: str):
    """A Firestore emulator importing export_dir, on free ports; yields host:port or None."""
    firebase = env("FIREBASE_BIN") or shutil.which("firebase")
    if not firebase or not shutil.which("java") or not os.path.isdir(os.path.join(export_dir, "firestore_export")):
        yield None
        return
    port, hub, logging_port = free_port(), free_port(), free_port()
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "firebase.json"), "w") as f:
        json.dump(
            {
                "emulators": {
                    "firestore": {"port": port, "host": "127.0.0.1"},
                    "hub": {"port": hub, "host": "127.0.0.1"},
                    "logging": {"port": logging_port, "host": "127.0.0.1"},
                    "ui": {"enabled": False},
                }
            },
            f,
        )
    logf = open(os.path.join(workdir, "emulator.log"), "w")
    environment = dict(os.environ, NO_UPDATE_NOTIFIER="1")
    environment.pop("FIRESTORE_EMULATOR_HOST", None)
    proc = subprocess.Popen(
        [firebase, "emulators:start", "--only", "firestore", "--project", PROJECT, "--import", export_dir],
        cwd=workdir,
        stdout=logf,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    try:
        ready = False
        deadline = time.time() + 180
        while time.time() < deadline and proc.poll() is None:
            with contextlib.suppress(OSError):
                with open(os.path.join(workdir, "emulator.log")) as lf:
                    if "All emulators ready" in lf.read():
                        ready = True
                        break
            time.sleep(1)
        yield f"127.0.0.1:{port}" if ready else None
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, 15)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=60)
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, 9)
        logf.close()


@contextlib.contextmanager
def throwaway_redis(redis_dir: str):
    binary = shutil.which("redis-server")
    if not binary or not os.path.isdir(redis_dir):
        yield None
        return
    port = free_port()
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--bind", "127.0.0.1", "--dir", redis_dir, "--appendonly", "yes", "--save", ""],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        up = wait_port(port, 30) and proc.poll() is None
        yield port if up else "failed"
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=20)


def full_counts(data_dir: str, workdir: str) -> dict:
    """Offline counts plus Firestore and Redis, read back through throwaway servers."""
    counts = offline_counts(data_dir)
    export_dir = os.path.join(data_dir, "firebase-export")
    counts["auth_users"] = count_auth_export(export_dir)
    with throwaway_firestore(export_dir, os.path.join(workdir, "firestore-emulator")) as host:
        if host:
            try:
                counts["firestore"] = firestore_counts(host)
            except (OSError, ValueError) as e:
                counts["firestore"] = {"error": str(e)}
        else:
            counts["firestore"] = {"skipped": "no firebase CLI/java here, or no Firestore export"}
    # Work on a copy: redis-server rewrites its directory on start.
    redis_copy = os.path.join(workdir, "redis")
    if os.path.isdir(os.path.join(data_dir, "redis")):
        shutil.copytree(os.path.join(data_dir, "redis"), redis_copy, dirs_exist_ok=True)
    with throwaway_redis(redis_copy) as port:
        if port == "failed":
            counts["redis_keys"] = "error: redis-server would not start on the restored data"
        elif port:
            try:
                counts["redis_keys"] = redis_command("127.0.0.1", port, "DBSIZE")
            except (OSError, RuntimeError) as e:
                counts["redis_keys"] = f"error: {e}"
        else:
            counts["redis_keys"] = "skipped (no redis-server here, or no redis data)"
    return counts


# ----------------------------------------------------------------------------- encryption


def encrypt_command(settings: Settings) -> tuple[list[str] | None, str]:
    mode = settings.encrypt
    if mode in ("", "none", "off", "0"):
        return None, ""
    if mode == "age":
        if not shutil.which("age"):
            raise BackupError("OMI_BACKUP_ENCRYPT=age but the age binary is not installed here")
        cmd = ["age", "--encrypt"]
        for r in settings.age_recipients:
            cmd += ["-r", r]
        if settings.age_recipients_file:
            cmd += ["-R", settings.age_recipients_file]
        if len(cmd) == 2:
            raise BackupError("OMI_BACKUP_ENCRYPT=age needs OMI_BACKUP_AGE_RECIPIENTS or ..._RECIPIENTS_FILE")
        return cmd, ".age"
    if mode == "gpg":
        if not shutil.which("gpg"):
            raise BackupError("OMI_BACKUP_ENCRYPT=gpg but gpg is not installed here")
        if not settings.gpg_recipient:
            raise BackupError("OMI_BACKUP_ENCRYPT=gpg needs OMI_BACKUP_GPG_RECIPIENT")
        return [
            "gpg",
            "--batch",
            "--yes",
            "--trust-model",
            "always",
            "--encrypt",
            "--recipient",
            settings.gpg_recipient,
            "--output",
            "-",
        ], ".gpg"
    raise BackupError(f"unknown OMI_BACKUP_ENCRYPT={mode} (none | age | gpg)")


def decrypt_command(archive: str, settings: Settings, identity: str = "") -> list[str] | None:
    if archive.endswith(".age"):
        ident = identity or settings.age_identity
        if not shutil.which("age"):
            raise BackupError("this archive is age-encrypted and the age binary is not installed here")
        if not ident:
            raise BackupError("this archive is age-encrypted: pass --identity or set OMI_BACKUP_AGE_IDENTITY")
        return ["age", "--decrypt", "-i", ident, archive]
    if archive.endswith(".gpg"):
        if not shutil.which("gpg"):
            raise BackupError("this archive is gpg-encrypted and gpg is not installed here")
        return ["gpg", "--batch", "--quiet", "--decrypt", archive]
    return None


@contextlib.contextmanager
def open_archive_stream(archive: str, settings: Settings, identity: str = ""):
    """The archive's plain .tar.gz bytes as a stream, decrypting on the fly."""
    cmd = decrypt_command(archive, settings, identity)
    if cmd is None:
        with open(archive, "rb") as f:
            yield f
        return
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        yield proc.stdout
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.stderr.close()
        if proc.wait() != 0:
            raise BackupError(f"decrypting failed: {err.strip()}")


# ----------------------------------------------------------------------------- backup


def backup(settings: Settings) -> dict:
    data_dir = os.path.abspath(settings.data_dir)
    backup_dir = settings.require_backup_dir()
    if not os.path.isdir(data_dir):
        raise BackupError(f"data directory {data_dir} does not exist")
    enc_cmd, enc_suffix = encrypt_command(settings)
    now = utc_now()
    name = "omi-backup-" + now.strftime("%Y%m%dT%H%M%SZ")
    archive = os.path.join(backup_dir, name + ".tar.gz" + enc_suffix)
    partial = archive + ".partial"
    warnings: list[str] = []

    log(f"snapshot {name}: data {data_dir} -> {backup_dir}")
    q_started = time.time()
    q = quiesce(settings, data_dir)
    if not q["ok"]:
        warnings.append(f"quiesce ({q['method']}) failed: {q['detail']}; using the last timed export")
        warn(warnings[-1])
    else:
        log(f"quiesce ({q['method']}) ok {q.get('detail', '')}".rstrip())

    export_meta = os.path.join(data_dir, "firebase-export", "firebase-export-metadata.json")
    export_version = ""
    if os.path.isfile(export_meta):
        with contextlib.suppress(OSError, ValueError):
            with open(export_meta) as f:
                export_version = json.load(f).get("version", "")
        age_s = time.time() - os.path.getmtime(export_meta)
        if q["method"] != "none" and q["ok"] and os.path.getmtime(export_meta) < q_started - 5:
            warnings.append(f"the Firestore export is {age_s / 60:.0f} min old although quiesce said ok")
    else:
        warnings.append("no Firestore export (firebase-export/) in the data dir: conversations are NOT in this backup")
        warn(warnings[-1])

    rdb = os.path.join(data_dir, "redis", "dump.rdb")
    redis_fresh = os.path.isfile(rdb) and os.path.getmtime(rdb) >= q_started - 5
    if not os.path.isfile(rdb):
        warnings.append("no redis/dump.rdb: Redis settings (model picks) are not in this backup")
    elif q["method"] != "none" and not redis_fresh:
        warnings.append("redis/dump.rdb predates this snapshot (BGSAVE did not run); the AOF is included as well")

    include_config = []
    if settings.config_dir:
        for fname in (".env", "docker-compose.override.yml"):
            path = os.path.join(settings.config_dir, fname)
            if not os.path.isfile(path):
                continue
            if fname == ".env" and not (settings.include_env and enc_cmd):
                if not settings.include_env:
                    warnings.append(".env is not in this backup (it holds keys); keep a copy in a password manager")
                else:
                    warnings.append(".env left out: OMI_BACKUP_INCLUDE_ENV=1 needs OMI_BACKUP_ENCRYPT=age or gpg")
                continue
            include_config.append((path, "config/" + fname))

    files: list[dict] = []
    components: dict[str, dict] = {}
    changed: list[str] = []
    out = open(partial, "wb")
    proc = None
    if enc_cmd:
        proc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.PIPE)
        sink = proc.stdin
    else:
        sink = out
    scratch = tempfile.mkdtemp(prefix=".omi-backup-", dir=backup_dir)
    try:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=sink, compresslevel=settings.gzip_level, mtime=0)
        tar = tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT)

        def add_file(src: str, arcname: str, component: str, display: str | None = None) -> None:
            with open(src, "rb") as f:
                st = os.fstat(f.fileno())
                ti = tarfile.TarInfo(arcname)
                ti.size, ti.mtime, ti.mode = st.st_size, int(st.st_mtime), st.st_mode & 0o7777
                reader = HashingReader(f, st.st_size)
                tar.addfile(ti, reader)
            if reader.short or os.path.getsize(src) != st.st_size:
                changed.append(display or arcname)
            files.append({"path": arcname, "sha256": reader.h.hexdigest(), "size": st.st_size})
            c = components.setdefault(component, {"files": 0, "bytes": 0})
            c["files"] += 1
            c["bytes"] += st.st_size

        def add_dir(src: str, arcname: str) -> None:
            ti = tarfile.TarInfo(arcname)
            ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, os.stat(src).st_mode & 0o7777, int(os.stat(src).st_mtime)
            tar.addfile(ti)

        def add_tree(rel: str, component: str, skip=(), transform=None) -> None:
            root = os.path.join(data_dir, rel)
            if not os.path.isdir(root):
                return
            for full, sub, is_dir in walk_files(root, skip):
                arc = "data/" + os.path.normpath(os.path.join(rel, sub))
                if is_dir:
                    add_dir(full, arc)
                elif transform:
                    transform(full, arc, component)
                else:
                    add_file(full, arc, component)

        add_tree("firebase-export", "firestore")
        if os.path.isfile(rdb):
            add_dir(os.path.join(data_dir, "redis"), "data/redis")
            add_file(rdb, "data/redis/dump.rdb", "redis")
            if not redis_fresh and q["method"] != "none":
                add_tree(os.path.join("redis", "appendonlydir"), "redis")
        elif os.path.isdir(os.path.join(data_dir, "redis", "appendonlydir")):
            add_tree(os.path.join("redis", "appendonlydir"), "redis")
        add_tree("storage", "storage")

        def chroma_file(full: str, arc: str, component: str) -> None:
            if full.endswith(("-wal", "-shm", "-journal")):
                return  # folded into the SQLite backup copy below
            if is_sqlite(full):
                copy = os.path.join(scratch, uuid.uuid4().hex)
                sqlite_snapshot(full, copy)
                add_file(copy, arc, component, display=arc)
                os.remove(copy)
            else:
                add_file(full, arc, component)

        add_tree("chroma", "vector_store", transform=chroma_file)
        collections = os.path.join(data_dir, "radicale", "collections")
        with radicale_shared_lock(collections):
            calendar_counts = count_radicale(collections)
            add_tree(os.path.join("radicale", "collections"), "calendar", skip=RADICALE_SKIP)
        add_tree("secrets", "secrets")
        for src, arc in include_config:
            add_file(src, arc, "config")

        counts = offline_counts(data_dir)
        counts["calendar"] = calendar_counts
        counts["auth_users"] = count_auth_export(os.path.join(data_dir, "firebase-export"))
        if settings.firestore:
            try:
                counts["firestore_live"] = firestore_counts(settings.firestore)
            except (OSError, ValueError) as e:
                warnings.append(f"live Firestore counts unavailable ({settings.firestore}): {e}")
        if changed:
            warnings.append(f"{len(changed)} file(s) changed while being read (archived as read): {changed[:5]}")

        manifest = {
            "format": FORMAT,
            "name": name,
            "created_at": iso(now),
            "git_commit": git_commit(settings),
            "versions": {
                "omi_backup_format": FORMAT,
                "firebase_export": export_version,
                "redis_rdb": _rdb_version(rdb),
                "python": sys.version.split()[0],
            },
            "quiesce": q,
            "components": components,
            "counts": counts,
            "warnings": warnings,
            "encrypted": settings.encrypt if enc_cmd else "none",
            "config_included": [arc for _, arc in include_config],
            "files": files,
        }
        blob = json.dumps(manifest, indent=2, sort_keys=True).encode()
        ti = tarfile.TarInfo(MANIFEST_NAME)
        ti.size, ti.mtime, ti.mode = len(blob), int(now.timestamp()), 0o600
        tar.addfile(ti, io.BytesIO(blob))
        tar.close()
        gz.close()
        if proc:
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "replace")
            if proc.wait() != 0:
                raise BackupError(f"encryption failed: {err.strip()}")
        out.flush()
        os.fsync(out.fileno())
        out.close()
        # Read back what landed on the backup disk (an NFS/SMB write can fail late).
        archive_sha = sha256_file(partial)
        archive_bytes = os.path.getsize(partial)
        os.replace(partial, archive)
        fsync_dir(backup_dir)
    except BaseException:
        with contextlib.suppress(Exception):
            out.close()
        if proc and proc.poll() is None:
            proc.kill()
        with contextlib.suppress(OSError):
            os.remove(partial)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    summary = {k: v for k, v in manifest.items() if k != "files"}
    summary.update(
        archive=os.path.basename(archive),
        archive_sha256=archive_sha,
        archive_bytes=archive_bytes,
        file_count=len(files),
        data_bytes=sum(f["size"] for f in files),
    )
    if settings.encrypt != "none" and enc_cmd:
        # Who is in it stays inside the encrypted archive.
        summary["counts"] = {}
    write_json_atomic(os.path.join(backup_dir, name + ".json"), summary)
    log(f"wrote {archive} ({human(archive_bytes)}, {len(files)} files, sha256 {archive_sha[:12]}...)")
    for w in warnings:
        warn(w)
    offsite(settings, [archive, os.path.join(backup_dir, name + ".json")])
    return summary


def _rdb_version(path: str) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(9)
        return head[5:].decode() if head.startswith(b"REDIS") else ""
    except OSError:
        return ""


def offsite(settings: Settings, paths: list[str]) -> None:
    mode = settings.offsite
    if not mode or mode == "none":
        return
    if mode == "restic":
        if not shutil.which("restic"):
            warn("OMI_BACKUP_OFFSITE=restic but restic is not installed here; offsite copy skipped")
            return
        environment = dict(os.environ)
        if settings.restic_repo:
            environment["RESTIC_REPOSITORY"] = settings.restic_repo
        cmd = ["restic", "backup", "--tag", "omi", *paths]
    elif mode == "rclone":
        if not shutil.which("rclone"):
            warn("OMI_BACKUP_OFFSITE=rclone but rclone is not installed here; offsite copy skipped")
            return
        if not settings.rclone_remote:
            warn("OMI_BACKUP_OFFSITE=rclone needs OMI_BACKUP_RCLONE_REMOTE (e.g. nas2:omi-backups)")
            return
        environment = dict(os.environ)
        cmd = ["rclone", "copy", "--no-traverse"]
        for p in paths:
            cmd += ["--include", os.path.basename(p)]
        cmd += [os.path.dirname(paths[0]), settings.rclone_remote]
    else:
        warn(f"unknown OMI_BACKUP_OFFSITE={mode} (restic | rclone)")
        return
    log(f"offsite copy with {mode}")
    res = subprocess.run(cmd, env=environment, capture_output=True, text=True)
    if res.returncode != 0:
        warn(f"offsite copy failed: {(res.stderr or res.stdout).strip()[-400:]}")
    else:
        log("offsite copy done")


# ----------------------------------------------------------------------------- listing / retention


def list_backups(backup_dir: str) -> list[dict]:
    """Complete backups (archive present), newest first."""
    found = {}
    for entry in os.listdir(backup_dir):
        for suffix in ARCHIVE_SUFFIXES:
            if entry.endswith(suffix):
                stem = entry[: -len(suffix)]
                stamp = parse_stamp(stem)
                if stamp:
                    found[stem] = {"name": stem, "time": stamp, "archive": os.path.join(backup_dir, entry)}
    for stem, item in found.items():
        meta = os.path.join(backup_dir, stem + ".json")
        item["meta"] = meta if os.path.isfile(meta) else None
    return sorted(found.values(), key=lambda b: b["time"], reverse=True)


def retention_keep(times: list[dt.datetime], daily: int, weekly: int, monthly: int) -> set[dt.datetime]:
    """Grandfather-father-son: the newest backup of each of the last N days, M ISO weeks, K months.

    Periods are counted from the newest backup's period back, by calendar (UTC), so a gap
    in backups does not stretch how far back a setting reaches. The newest backup is
    always kept.
    """
    if not times:
        return set()
    ordered = sorted(times, reverse=True)
    newest = ordered[0]
    keep = {newest}

    def pick(period_of, count, step_back):
        if count <= 0:
            return
        allowed, p = set(), period_of(newest)
        for _ in range(count):
            allowed.add(p)
            p = step_back(p)
        seen = set()
        for t in ordered:
            period = period_of(t)
            if period in allowed and period not in seen:
                seen.add(period)
                keep.add(t)

    pick(lambda t: t.date(), daily, lambda d: d - dt.timedelta(days=1))

    def week_of(t):
        y, w, _ = t.isocalendar()
        return (y, w)

    def prev_week(p):
        monday = dt.date.fromisocalendar(p[0], p[1], 1) - dt.timedelta(days=7)
        y, w, _ = monday.isocalendar()
        return (y, w)

    pick(week_of, weekly, prev_week)
    pick(lambda t: (t.year, t.month), monthly, lambda p: (p[0] - 1, 12) if p[1] == 1 else (p[0], p[1] - 1))
    return keep


def prune(settings: Settings, dry_run: bool = False) -> list[str]:
    backup_dir = settings.require_backup_dir()
    backups = list_backups(backup_dir)
    keep = retention_keep([b["time"] for b in backups], settings.keep_daily, settings.keep_weekly, settings.keep_monthly)
    removed = []
    for b in backups:
        if b["time"] in keep:
            continue
        for path in (b["archive"], b["meta"]):
            if path and os.path.exists(path):
                if not dry_run:
                    os.remove(path)
                removed.append(path)
    # Leftovers of interrupted runs (older than a day).
    for entry in os.listdir(backup_dir):
        path = os.path.join(backup_dir, entry)
        if (entry.endswith(".partial") or entry.startswith(".omi-backup-")) and (
            time.time() - os.path.getmtime(path) > 86400
        ):
            if not dry_run:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            removed.append(path)
    for path in removed:
        log(("would remove " if dry_run else "removed ") + path)
    log(
        f"retention: {len(keep)} kept (daily {settings.keep_daily}, weekly {settings.keep_weekly},"
        f" monthly {settings.keep_monthly})"
    )
    return removed


def prune_after(settings: Settings, summary: dict) -> None:
    """Retention runs only after a clean snapshot: a degraded one must not push out a good one."""
    if not (summary.get("quiesce") or {}).get("ok", True):
        warn("retention skipped: this snapshot's quiesce failed, older backups are kept")
        return
    prune(settings)


# ----------------------------------------------------------------------------- restore


def resolve_archive(spec: str, settings: Settings) -> tuple[str, dict | None]:
    if spec in ("", "latest"):
        backups = list_backups(settings.require_backup_dir())
        if not backups:
            raise BackupError(f"no backups in {settings.backup_dir}")
        spec = backups[0]["archive"]
    if spec.endswith(".json"):
        with open(spec) as f:
            meta = json.load(f)
        return os.path.join(os.path.dirname(spec), meta["archive"]), meta
    if not os.path.isfile(spec):
        raise BackupError(f"{spec} not found")
    stem = os.path.basename(spec)
    for suffix in ARCHIVE_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    meta_path = os.path.join(os.path.dirname(spec), stem + ".json")
    meta = None
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    return spec, meta


def _safe_member(name: str) -> bool:
    return not (name.startswith("/") or ".." in name.split("/"))


def verify_archive(archive: str, meta: dict | None, settings: Settings, identity: str = "") -> dict:
    """Pass 1: the archive's own checksum, then every file's, without writing anything."""
    if meta and meta.get("archive_sha256"):
        actual = sha256_file(archive)
        if actual != meta["archive_sha256"]:
            raise BackupError(f"archive checksum mismatch: {archive} is damaged or was changed")
        log("archive checksum ok")
    else:
        warn("no .json next to the archive: its outer checksum cannot be checked (file checksums still are)")
    seen: dict[str, tuple[str, int]] = {}
    manifest = None
    with open_archive_stream(archive, settings, identity) as stream:
        with tarfile.open(fileobj=stream, mode="r|gz") as tar:
            for member in tar:
                if not _safe_member(member.name):
                    raise BackupError(f"unsafe path in archive: {member.name}")
                if member.issym() or member.islnk():
                    raise BackupError(f"links are not expected in a backup: {member.name}")
                if not member.isfile():
                    continue
                f = tar.extractfile(member)
                if member.name == MANIFEST_NAME:
                    manifest = json.loads(f.read())
                    continue
                h = hashlib.sha256()
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
                seen[member.name] = (h.hexdigest(), member.size)
    if manifest is None:
        raise BackupError("the archive has no MANIFEST.json: not an omi backup, or cut short")
    expected = {f["path"]: (f["sha256"], f["size"]) for f in manifest["files"]}
    bad = [p for p, v in expected.items() if seen.get(p) != v]
    extra = sorted(set(seen) - set(expected))
    if bad or extra:
        raise BackupError(f"checksum check failed: {len(bad)} file(s) differ or are missing {bad[:5]}, extra {extra[:5]}")
    log(f"all {len(expected)} file checksums ok")
    return manifest


MANAGED = ("firebase-export", "firebase-export.prev", "redis", "storage", "chroma", "radicale", "secrets")


def existing_state(data_dir: str) -> list[str]:
    """Managed paths in data_dir that hold anything."""
    found = []
    for name in MANAGED:
        path = os.path.join(data_dir, name)
        if os.path.isfile(path):
            found.append(name)
        elif os.path.isdir(path) and any(filenames for _, _, filenames in os.walk(path)):
            found.append(name)
    return found


def restore(
    spec: str,
    data_dir: str,
    settings: Settings,
    *,
    dry_run: bool = False,
    force: bool = False,
    identity: str = "",
    config_dir: str = "",
    skip_running_check: bool = False,
) -> dict:
    archive, meta = resolve_archive(spec, settings)
    data_dir = os.path.abspath(data_dir)
    log(f"restore {archive} -> {data_dir}{' (dry run)' if dry_run else ''}")
    manifest = verify_archive(archive, meta, settings, identity)
    if not skip_running_check and container_running(settings.container):
        raise BackupError(f"container {settings.container} is running: stop the stack first (docker compose down)")
    present = existing_state(data_dir) if os.path.isdir(data_dir) else []
    plan = sorted({f["path"].split("/")[1] for f in manifest["files"] if f["path"].startswith("data/")})
    log(f"backup from {manifest['created_at']} (commit {manifest.get('git_commit', '?')[:12]}): restores {', '.join(plan)}")
    if manifest.get("config_included"):
        where = config_dir or "(not written: pass --config-dir)"
        log(f"config files {manifest['config_included']} -> {where}")
    if present:
        log(f"the target already holds: {', '.join(present)}")
    if dry_run:
        if present and not force:
            log("a real run would refuse without --force (the target is not empty)")
        elif present:
            log(f"a real run would move those aside into {data_dir}/.pre-restore-<time>/")
        log("dry run: nothing written")
        return {"archive": archive, "plan": plan, "present": present, "dry_run": True}
    if present and not force:
        raise BackupError(
            f"{data_dir} already holds {', '.join(present)}; pass --force to move it aside and restore"
        )
    os.makedirs(data_dir, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    staging = os.path.join(data_dir, f".restore-{stamp}.tmp")
    os.makedirs(staging)
    expected = {f["path"]: (f["sha256"], f["size"]) for f in manifest["files"]}
    try:
        # Pass 2: extract, hashing again as the bytes land.
        with open_archive_stream(archive, settings, identity) as stream:
            with tarfile.open(fileobj=stream, mode="r|gz") as tar:
                for member in tar:
                    if member.name == MANIFEST_NAME or not _safe_member(member.name):
                        continue
                    target = os.path.join(staging, member.name)
                    if member.isdir():
                        os.makedirs(target, exist_ok=True)
                        os.chmod(target, member.mode or 0o755)
                        continue
                    if not member.isfile():
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    src = tar.extractfile(member)
                    h = hashlib.sha256()
                    with open(target, "wb") as out:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            h.update(chunk)
                            out.write(chunk)
                    os.chmod(target, member.mode or 0o644)
                    os.utime(target, (member.mtime, member.mtime))
                    if expected.get(member.name) != (h.hexdigest(), member.size):
                        raise BackupError(f"{member.name} changed between the check and the extraction")
        moved_aside = None
        if present:
            moved_aside = os.path.join(data_dir, f".pre-restore-{stamp}")
            os.makedirs(moved_aside)
            for name in present + [n for n in MANAGED if n not in present and os.path.exists(os.path.join(data_dir, n))]:
                os.rename(os.path.join(data_dir, name), os.path.join(moved_aside, name))
            log(f"previous state moved to {moved_aside} (delete it once the restore looks right)")
        restored_root = os.path.join(staging, "data")
        for name in sorted(os.listdir(restored_root)) if os.path.isdir(restored_root) else []:
            dest = os.path.join(data_dir, name)
            if os.path.exists(dest):
                # Only empty leftovers can be here (present[] covered the rest).
                shutil.rmtree(dest) if os.path.isdir(dest) else os.remove(dest)
            os.rename(os.path.join(restored_root, name), dest)
        _redis_aof_from_rdb(os.path.join(data_dir, "redis"))
        config_root = os.path.join(staging, "config")
        if os.path.isdir(config_root) and config_dir:
            os.makedirs(config_dir, exist_ok=True)
            for name in os.listdir(config_root):
                dest = os.path.join(config_dir, name)
                if os.path.exists(dest) and not force:
                    warn(f"{dest} exists; left as is (pass --force to replace it)")
                    continue
                shutil.copy2(os.path.join(config_root, name), dest)
                log(f"restored {dest}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log("restore complete: start the stack (docker compose up -d); the emulator imports firebase-export on start")
    return {"archive": archive, "plan": plan, "present": present, "moved_aside": moved_aside}


def _redis_aof_from_rdb(redis_dir: str) -> None:
    """With appendonly yes and no AOF, Redis 7 starts EMPTY and ignores dump.rdb.

    Seed the AOF from the snapshot: the RDB becomes the AOF base file.
    """
    rdb = os.path.join(redis_dir, "dump.rdb")
    aof_dir = os.path.join(redis_dir, "appendonlydir")
    if not os.path.isfile(rdb) or os.path.isdir(aof_dir):
        return
    os.makedirs(aof_dir)
    shutil.copy2(rdb, os.path.join(aof_dir, "appendonly.aof.1.base.rdb"))
    with open(os.path.join(aof_dir, "appendonly.aof.manifest"), "w") as f:
        f.write("file appendonly.aof.1.base.rdb seq 1 type b\n")


# ----------------------------------------------------------------------------- verify (test restore)


def verify(spec: str, settings: Settings, identity: str = "", keep: bool = False) -> dict:
    archive, meta = resolve_archive(spec, settings)
    base = settings.verify_tmp or (settings.backup_dir if settings.backup_dir else tempfile.gettempdir())
    os.makedirs(base, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".omi-verify-", dir=base)
    report = {"archive": os.path.basename(archive), "started_at": iso(utc_now()), "checks": [], "ok": False}

    def check(name, ok, detail="", level="FAIL"):
        report["checks"].append({"name": name, "result": "PASS" if ok else level, "detail": str(detail)})
        return ok

    try:
        data = os.path.join(work, "data")
        try:
            restore(archive, data, settings, identity=identity, skip_running_check=True)
            check("restore into a scratch directory (checksums verified twice)", True, data)
        except BackupError as e:
            check("restore into a scratch directory", False, e)
            raise
        manifest_counts = {}
        # The inner manifest has the counts even when the outer one is redacted.
        with open_archive_stream(archive, settings, identity) as stream:
            with tarfile.open(fileobj=stream, mode="r|gz") as tar:
                for member in tar:
                    if member.name == MANIFEST_NAME:
                        manifest_counts = json.loads(tar.extractfile(member).read()).get("counts") or {}
        counts = full_counts(data, os.path.join(work, "services"))
        report["counts"] = counts
        report["counts_at_backup"] = manifest_counts

        check(
            "Firestore export present",
            os.path.isfile(os.path.join(data, "firebase-export", "firebase-export-metadata.json")),
        )
        fs = counts.get("firestore") or {}
        if "skipped" in fs or "error" in fs:
            check("Firestore read back", False, fs.get("skipped") or fs.get("error"), level="WARN")
        else:
            check(
                "Firestore read back: conversations / tasks / users",
                True,
                f"{fs.get('conversations')} conversations, {fs.get('action_items')} tasks, {fs.get('users')} users",
            )
            live = manifest_counts.get("firestore_live") or {}
            for group in ("conversations", "action_items"):
                if group in live and live[group] > 0 and fs.get(group, 0) == 0:
                    check(f"{group} restored", False, f"0 restored, {live[group]} live at backup time")
                elif group in live and live[group] != fs.get(group):
                    check(
                        f"{group} vs live count at backup time",
                        False,
                        f"{fs.get(group)} restored, {live[group]} live (writes between export and count?)",
                        level="WARN",
                    )
        cal, cal_then = counts["calendar"], manifest_counts.get("calendar") or {}
        check(
            "calendar items match the backup",
            not cal_then or cal == cal_then,
            f"{cal['calendars']} calendars, {cal['events']} events, {cal['tasks']} tasks"
            + ("" if not cal_then or cal == cal_then else f" (backup said {cal_then})"),
        )
        vec, vec_then = counts["vector_store"], manifest_counts.get("vector_store") or {}
        check(
            "vector store matches the backup",
            not vec_then or vec == vec_then,
            f"{vec['embeddings']} embeddings in {vec['sqlite_files']} SQLite file(s)",
        )
        st, st_then = counts["storage"], manifest_counts.get("storage") or {}
        check(
            "stored files (audio, profiles) match the backup",
            not st_then or st == st_then,
            f"{st['files']} files, {human(st['bytes'])}",
        )
        rk = counts.get("redis_keys")
        check(
            "Redis loads the snapshot",
            isinstance(rk, int),
            f"{rk} keys" if isinstance(rk, int) else rk,
            level="FAIL" if str(rk).startswith("error") else "WARN",
        )
        report["ok"] = not any(c["result"] == "FAIL" for c in report["checks"])
    except BackupError as e:
        report["error"] = str(e)
    finally:
        if keep:
            log(f"--keep: scratch restore left in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    report["finished_at"] = iso(utc_now())
    print_report(report)
    if settings.backup_dir and os.path.isdir(settings.backup_dir):
        vdir = os.path.join(settings.backup_dir, "verify")
        os.makedirs(vdir, exist_ok=True)
        stem = os.path.basename(archive).split(".tar.gz")[0]
        write_json_atomic(os.path.join(vdir, stem + ".verify.json"), report)
        write_json_atomic(os.path.join(vdir, "latest.json"), report)
    return report


def print_report(report: dict) -> None:
    print(f"\nTest restore of {report['archive']}")
    width = max((len(c["name"]) for c in report["checks"]), default=10)
    for c in report["checks"]:
        print(f"  {c['result']:<4}  {c['name']:<{width}}  {c['detail']}")
    if report.get("error"):
        print(f"  error: {report['error']}")
    print(f"Result: {'OK' if report['ok'] else 'FAILED'}\n")


# ----------------------------------------------------------------------------- schedule (sidecar)


def schedule(settings: Settings) -> None:
    settings.require_backup_dir()
    try:
        hh, mm = (int(x) for x in settings.time.split(":"))
    except ValueError:
        raise BackupError("OMI_BACKUP_TIME must be HH:MM (local time of this container, TZ)")
    log(
        f"scheduled: backup daily at {settings.time} ({time.strftime('%Z')}), test restore on day "
        f"{settings.verify_day} of each month (0 = off), into {settings.backup_dir}"
    )
    last_day = None
    while True:
        now = dt.datetime.now()
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now >= due and last_day != now.date():
            last_day = now.date()
            if _backup_exists_since(settings.backup_dir, due):
                log("today's backup already exists; next run tomorrow")
            else:
                try:
                    prune_after(settings, backup(settings))
                except Exception as e:  # noqa: BLE001 - the loop must survive one bad night
                    warn(f"backup failed: {e}")
                if settings.verify_day and now.day == settings.verify_day:
                    try:
                        verify("latest", settings)
                    except Exception as e:  # noqa: BLE001
                        warn(f"test restore failed: {e}")
        time.sleep(30)


def _backup_exists_since(backup_dir: str, local_due: dt.datetime) -> bool:
    due_utc = local_due.astimezone(dt.timezone.utc)
    return any(b["time"] >= due_utc for b in list_backups(backup_dir))


# ----------------------------------------------------------------------------- cli


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backups of a self-hosted Omi stack (BACKUP.md)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup", help="take a snapshot now")
    b.add_argument("--data-dir")
    b.add_argument("--backup-dir")
    b.add_argument("--quiesce", choices=("auto", "docker", "flag", "cmd", "none"))
    b.add_argument("--no-prune", action="store_true")
    r = sub.add_parser("restore", help="restore an archive into a stopped stack")
    r.add_argument("archive", nargs="?", default="latest", help="archive, its .json, or 'latest'")
    r.add_argument("--data-dir")
    r.add_argument("--backup-dir")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--force", action="store_true", help="move existing state aside and restore")
    r.add_argument("--identity", default="", help="age identity file for an .age archive")
    r.add_argument("--config-dir", default="", help="where to put a backed-up .env / override file")
    v = sub.add_parser("verify", help="test restore into a scratch dir and read counts back")
    v.add_argument("archive", nargs="?", default="latest")
    v.add_argument("--backup-dir")
    v.add_argument("--identity", default="")
    v.add_argument("--keep", action="store_true")
    p = sub.add_parser("prune", help="apply retention")
    p.add_argument("--backup-dir")
    p.add_argument("--dry-run", action="store_true")
    ls = sub.add_parser("list", help="list backups")
    ls.add_argument("--backup-dir")
    c = sub.add_parser("counts", help="counts of a data dir and/or a live stack")
    c.add_argument("--data-dir")
    c.add_argument("--firestore", help="live Firestore emulator host:port")
    c.add_argument("--auth", help="live Auth emulator host:port")
    c.add_argument("--redis", help="live Redis host:port")
    sc = sub.add_parser("schedule", help="the sidecar loop")
    sc.add_argument("--backup-dir")
    args = parser.parse_args(argv)

    settings = Settings()
    if getattr(args, "backup_dir", None):
        settings.backup_dir = args.backup_dir
    if getattr(args, "data_dir", None):
        settings.data_dir = args.data_dir
    try:
        if args.cmd == "backup":
            if args.quiesce:
                settings.quiesce = args.quiesce
            summary = backup(settings)
            if not args.no_prune:
                prune_after(settings, summary)
        elif args.cmd == "restore":
            restore(
                args.archive,
                settings.data_dir,
                settings,
                dry_run=args.dry_run,
                force=args.force,
                identity=args.identity,
                config_dir=args.config_dir,
            )
        elif args.cmd == "verify":
            return 0 if verify(args.archive, settings, args.identity, args.keep)["ok"] else 1
        elif args.cmd == "prune":
            prune(settings, args.dry_run)
        elif args.cmd == "list":
            for item in list_backups(settings.require_backup_dir()):
                meta = {}
                if item["meta"]:
                    with open(item["meta"]) as f:
                        meta = json.load(f)
                size = human(meta.get("archive_bytes", os.path.getsize(item["archive"])))
                warnings = len(meta.get("warnings") or [])
                print(f"{item['name']}  {size:>9}  {os.path.basename(item['archive'])}  warnings: {warnings}")
        elif args.cmd == "counts":
            out = {}
            if args.data_dir:
                out.update(offline_counts(args.data_dir))
            if args.firestore:
                out["firestore"] = firestore_counts(args.firestore)
            if args.auth:
                out["auth_users"] = auth_count_live(args.auth)
            if args.redis:
                host, port = args.redis.rsplit(":", 1)
                out["redis_keys"] = redis_command(host, int(port), "DBSIZE")
            print(json.dumps(out, indent=2, sort_keys=True))
        elif args.cmd == "schedule":
            schedule(settings)
    except BackupError as e:
        print(f"omi-backup: error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
