#!/usr/bin/env python3
"""omi-health: a watchdog for a self-hosted Omi stack. Standard library only.

Every OMI_HEALTH_INTERVAL_S (60) it checks each part of the stack, serves a status
page on the LAN (HTML at /, JSON at /status.json), and sends an alert when a check
CHANGES state: down, degraded, recovered. HEALTH.md is the operator's guide.

States: ok | degraded | down | off (not configured here; never alerts).

Alerts go to whichever of these are configured (none is a cloud service by default):
  ntfy     OMI_HEALTH_NTFY_URL + OMI_HEALTH_NTFY_TOPIC (+ OMI_HEALTH_NTFY_TOKEN)
  Gotify   OMI_HEALTH_GOTIFY_URL + OMI_HEALTH_GOTIFY_TOKEN (an application token)
  webhook  OMI_HEALTH_WEBHOOK_URL (+ OMI_HEALTH_WEBHOOK_TOKEN, sent as a Bearer token)

When an alert is sent (the state machine, Tracker below):
  * a raw result must repeat OMI_HEALTH_FAIL_AFTER (2) times in a row before a check
    counts as down/degraded, and OMI_HEALTH_RECOVER_AFTER (1) times before it counts
    as ok again, so one slow answer does not page anyone;
  * a problem alert (down, degraded) for a check waits until OMI_HEALTH_COOLDOWN_S
    (900) after that check's previous problem alert; a problem that is still there
    when the cooldown ends is sent then. Getting worse (degraded -> down) is sent at once;
  * "recovered" is sent at once, but only for a check whose problem was announced;
  * OMI_HEALTH_DIGEST_AT=HH:MM (off by default) sends one summary a day.
"""

from __future__ import annotations

import base64
import concurrent.futures
import datetime as dt
import html
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OK, DEGRADED, DOWN, OFF = "ok", "degraded", "down", "off"
SEVERITY = {OFF: -1, OK: 0, DEGRADED: 1, DOWN: 2}


def env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "")
    return v.strip() if v.strip() else default


def env_url(name: str, default: str) -> str:
    """A URL/address setting; "off" switches that check off."""
    value = env(name, default)
    return "" if value.lower() == "off" else value.rstrip("/")


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def log(msg: str) -> None:
    print(f"omi-health: {msg}", flush=True)


class Result:
    def __init__(self, state: str, detail: str = "", latency_ms: float | None = None):
        self.state, self.detail, self.latency_ms = state, detail, latency_ms

    def __repr__(self) -> str:
        return f"Result({self.state!r}, {self.detail!r})"


# ----------------------------------------------------------------------------- state machine


class Event:
    def __init__(self, kind: str, check: str, label: str, state: str, previous: str, detail: str, since: float, now: float):
        self.kind, self.check, self.label = kind, check, label
        self.state, self.previous, self.detail = state, previous, detail
        self.since, self.at = since, now

    @property
    def duration_s(self) -> int:
        return int(self.at - self.since)

    def title(self, host: str) -> str:
        word = {"down": "DOWN", "degraded": "degraded", "recovered": "recovered"}[self.kind]
        return f"[{host}] {self.label} {word}"

    def message(self) -> str:
        if self.kind == "recovered":
            return f"{self.label} is ok again after {fmt_duration(self.duration_s)} ({self.previous}). {self.detail}".strip()
        return f"{self.label} is {self.state}: {self.detail}".strip()

    def as_dict(self, host: str, status_url: str) -> dict:
        return {
            "source": "omi-health",
            "event": self.kind,
            "check": self.check,
            "label": self.label,
            "state": self.state,
            "previous": self.previous,
            "detail": self.detail,
            "title": self.title(host),
            "message": self.message(),
            "since": iso(self.since),
            "duration_s": self.duration_s,
            "at": iso(self.at),
            "host": host,
            "status_url": status_url,
        }


class Tracker:
    """One check's confirmed state and what has been announced about it."""

    def __init__(self, name: str, label: str, fail_after: int = 2, recover_after: int = 1, cooldown_s: float = 900):
        self.name, self.label = name, label
        self.fail_after, self.recover_after, self.cooldown_s = max(1, fail_after), max(1, recover_after), cooldown_s
        self.state = OFF  # confirmed
        self.since = time.time()
        self.detail = ""
        self.latency_ms = None
        self.checked_at = 0.0
        self.raw = OFF
        self.candidate, self.streak, self.candidate_at = None, 0, 0.0
        self.announced = OK  # what the last alert said (OK = nothing outstanding)
        self.problem_alert_at = None  # last down/degraded alert
        self.problem_since = None
        self.first = True

    def observe(self, result: Result, now: float) -> list[Event]:
        self.raw, self.detail, self.latency_ms, self.checked_at = result.state, result.detail, result.latency_ms, now
        if self.first:
            # Nothing is known yet: an OFF/OK start is taken as is, a problem still needs confirming.
            self.first = False
            if result.state in (OK, OFF):
                self._set(result.state, now)
                return []
            self.state = OK
        if result.state == self.state:
            self.candidate, self.streak = None, 0
        else:
            if result.state == self.candidate:
                self.streak += 1
            else:
                # Remember when it started: "since" and durations count from the first bad result.
                self.candidate, self.streak, self.candidate_at = result.state, 1, now
            needed = self.recover_after if result.state in (OK, OFF) else self.fail_after
            if self.streak >= needed:
                self._set(result.state, self.candidate_at)
                self.candidate, self.streak = None, 0
        return self._alerts(now)

    def _set(self, state: str, now: float) -> None:
        if state != self.state:
            if state in (DOWN, DEGRADED) and self.state not in (DOWN, DEGRADED):
                self.problem_since = now
            self.state, self.since = state, now

    def _alerts(self, now: float) -> list[Event]:
        state = self.state
        if state == OFF:
            # Turned off/unconfigured: forget any outstanding problem quietly.
            self.announced = OK
            return []
        if state == self.announced:
            return []
        if state == OK:
            previous = self.announced
            self.announced = OK
            since = self.problem_since or self.since
            self.problem_since = None
            return [Event("recovered", self.name, self.label, OK, previous, self.detail, since, now)]
        worse = SEVERITY[state] > SEVERITY[self.announced]
        cooled = self.problem_alert_at is None or now - self.problem_alert_at >= self.cooldown_s
        if not (cooled or (worse and self.announced != OK)):
            return []  # held back; sent when the cooldown ends if still true
        previous = self.announced
        self.announced, self.problem_alert_at = state, now
        return [Event(state, self.name, self.label, state, previous, self.detail, self.problem_since or self.since, now)]

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "state": self.state,
            "raw": self.raw,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "since": iso(self.since),
            "checked_at": iso(self.checked_at) if self.checked_at else None,
        }

    def dump(self) -> dict:
        return {
            "state": self.state,
            "since": self.since,
            "announced": self.announced,
            "problem_alert_at": self.problem_alert_at,
            "problem_since": self.problem_since,
        }

    def load(self, data: dict) -> None:
        self.state = data.get("state", OFF)
        self.since = data.get("since", time.time())
        self.announced = data.get("announced", OK)
        self.problem_alert_at = data.get("problem_alert_at")
        self.problem_since = data.get("problem_since")
        self.first = False


def fmt_duration(s: float) -> str:
    s = int(s)
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} days"


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- notifiers


def http_post(url: str, body: bytes, headers: dict, timeout: float = 10) -> int:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
        return resp.status


NTFY_PRIORITY = {"down": "5", "degraded": "4", "recovered": "3", "digest": "2", "test": "3"}
NTFY_TAGS = {
    "down": "rotating_light",
    "degraded": "warning",
    "recovered": "white_check_mark",
    "digest": "clipboard",
    "test": "bell",
}
GOTIFY_PRIORITY = {"down": 8, "degraded": 5, "recovered": 4, "digest": 2, "test": 4}


class Notifier:
    name = "notifier"

    def build(self, kind: str, title: str, message: str, payload: dict) -> tuple[str, bytes, dict]:
        raise NotImplementedError

    def send(self, kind: str, title: str, message: str, payload: dict, retries: int = 3) -> None:
        url, body, headers = self.build(kind, title, message, payload)
        last = None
        for attempt in range(retries):
            try:
                http_post(url, body, headers)
                return
            except (urllib.error.URLError, OSError) as e:
                last = e
                time.sleep(min(2**attempt, 10) * (0.1 if os.environ.get("OMI_HEALTH_FAST_RETRY") else 1))
        raise RuntimeError(f"{self.name}: {last}")


class Ntfy(Notifier):
    name = "ntfy"

    def __init__(self, url: str, topic: str, token: str = "", click: str = ""):
        self.url, self.topic, self.token, self.click = url.rstrip("/"), topic.strip("/"), token, click

    def build(self, kind, title, message, payload):
        headers = {
            "Title": title.encode("ascii", "replace").decode(),
            "Priority": NTFY_PRIORITY.get(kind, "3"),
            "Tags": NTFY_TAGS.get(kind, "bell"),
            "Content-Type": "text/plain; charset=utf-8",
        }
        if self.click:
            headers["Click"] = self.click
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return f"{self.url}/{urllib.parse.quote(self.topic)}", message.encode(), headers


class Gotify(Notifier):
    name = "gotify"

    def __init__(self, url: str, token: str, click: str = ""):
        self.url, self.token, self.click = url.rstrip("/"), token, click

    def build(self, kind, title, message, payload):
        body = {"title": title, "message": message, "priority": GOTIFY_PRIORITY.get(kind, 4)}
        if self.click:
            body["extras"] = {"client::notification": {"click": {"url": self.click}}}
        headers = {"Content-Type": "application/json", "X-Gotify-Key": self.token}
        return f"{self.url}/message", json.dumps(body).encode(), headers


class Webhook(Notifier):
    name = "webhook"

    def __init__(self, url: str, token: str = ""):
        self.url, self.token = url, token

    def build(self, kind, title, message, payload):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return self.url, json.dumps(payload).encode(), headers


def notifiers_from_env() -> list[Notifier]:
    click = env("OMI_HEALTH_PUBLIC_URL")
    out: list[Notifier] = []
    if env("OMI_HEALTH_NTFY_URL") and env("OMI_HEALTH_NTFY_TOPIC"):
        out.append(Ntfy(env("OMI_HEALTH_NTFY_URL"), env("OMI_HEALTH_NTFY_TOPIC"), env("OMI_HEALTH_NTFY_TOKEN"), click))
    if env("OMI_HEALTH_GOTIFY_URL") and env("OMI_HEALTH_GOTIFY_TOKEN"):
        out.append(Gotify(env("OMI_HEALTH_GOTIFY_URL"), env("OMI_HEALTH_GOTIFY_TOKEN"), click))
    if env("OMI_HEALTH_WEBHOOK_URL"):
        out.append(Webhook(env("OMI_HEALTH_WEBHOOK_URL"), env("OMI_HEALTH_WEBHOOK_TOKEN")))
    return out


# ----------------------------------------------------------------------------- checks


def http_get(url: str, timeout: float, headers: dict | None = None, method: str = "GET", body: bytes | None = None):
    """(status, body bytes, latency ms); HTTP errors return their status instead of raising."""
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=body)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(1 << 20)
            return resp.status, data, (time.monotonic() - started) * 1000
    except urllib.error.HTTPError as e:
        return e.code, e.read(1 << 16), (time.monotonic() - started) * 1000


def short_error(e: Exception) -> str:
    reason = getattr(e, "reason", None) or e
    return str(reason)[:160]


def basic_auth(user: str, password: str) -> dict:
    if not user or not password:
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class Checks:
    """The stack's checks. Each method returns a Result; a missing setting means OFF."""

    def __init__(self) -> None:
        self.timeout = env_float("OMI_HEALTH_TIMEOUT_S", 5)
        self.slow_ms = env_float("OMI_HEALTH_SLOW_MS", 3000)
        self.backend = env_url("OMI_HEALTH_BACKEND_URL", "http://omi-local:8000")
        self.firestore = env_url("OMI_HEALTH_FIRESTORE_URL", "http://omi-local:8085")
        self.redis = env_url("OMI_HEALTH_REDIS", "omi-local:6380")
        self.redis_password = env("REDIS_DB_PASSWORD")
        bundled = env("OMI_BUNDLED_CALDAV", "1") != "0"
        self.caldav = env_url("OMI_HEALTH_CALDAV_URL", "http://omi-local:5232" if bundled else "off")
        self.caldav_user = env("CALDAV_USER", "omi")
        self.caldav_password_file = env("OMI_HEALTH_CALDAV_PASSWORD_FILE", "/data/secrets/caldav_password")
        audio = env("OMI_AUDIO_URL")
        self.audio = env_url("OMI_HEALTH_AUDIO_URL", "off" if audio == "off" else (audio or "http://omi-audio:8790"))
        self.audio_max_in_flight = env_float("OMI_HEALTH_AUDIO_MAX_IN_FLIGHT", 4)
        self.llm = env_url("OMI_HEALTH_LLM_URL", "http://omi-proxy:4000")
        self.llm_slow_ms = env_float("OMI_HEALTH_LLM_SLOW_MS", 5000)
        self.adapter = env_url("OMI_HEALTH_LLM_ADAPTER_URL", "http://omi-local:8788")
        hermes = env_url("OMI_HEALTH_HERMES_URL", env("HERMES_API_URL", "off"))
        self.hermes = "" if "CHANGE-ME" in hermes else hermes
        self.hermes_key = env("HERMES_API_KEY")
        self.dashboard_auth = basic_auth(env("DASHBOARD_USERNAME"), env("DASHBOARD_PASSWORD"))
        self.disks = []
        for part in env("OMI_HEALTH_DISKS", "data=/data,backups=/backups").split(","):
            if "=" in part:
                label, path = part.split("=", 1)
                self.disks.append((label.strip(), path.strip()))
        self.disk_warn = env_float("OMI_HEALTH_DISK_WARN_PCT", 15)
        self.disk_crit = env_float("OMI_HEALTH_DISK_CRIT_PCT", 5)
        self.gpu_warn = env_float("OMI_HEALTH_GPU_WARN_PCT", 95)
        self.backup_dir = env("OMI_HEALTH_BACKUP_DIR", "/backups")
        self.backup_max_age_h = env_float("OMI_HEALTH_BACKUP_MAX_AGE_H", 26)
        self._components = None
        self._components_lock = threading.Lock()

    def all(self) -> list[tuple[str, str, object]]:
        items = [
            ("backend", "Backend API", self.check_backend),
            ("firestore", "Firestore emulator", self.check_firestore),
            ("auth", "Auth emulator", lambda: self.component("auth_emulator")),
            ("vector_db", "Vector store", lambda: self.component("vector_db")),
            ("redis", "Redis", self.check_redis),
            ("caldav", "CalDAV (Radicale)", self.check_caldav),
            ("audio", "Audio / STT (omi-audio)", self.check_audio),
            ("llm", "LLM (merge proxy)", self.check_llm),
            ("llm_adapter", "LLM adapter", self.check_adapter),
            ("hermes", "Hermes", self.check_hermes),
        ]
        for label, path in self.disks:
            items.append((f"disk_{label}", f"Disk: {label}", lambda path=path: self.check_disk(path)))
        items.append(("gpu", "GPU memory", self.check_gpu))
        items.append(("backup", "Last backup", self.check_backup))
        return items

    def start_round(self) -> None:
        with self._components_lock:
            self._components = None

    # --- backend & what its dashboard knows (the auth emulator only listens inside omi-local)

    def check_backend(self) -> Result:
        if not self.backend:
            return Result(OFF, "OMI_HEALTH_BACKEND_URL empty")
        try:
            status, _, ms = http_get(self.backend + "/health", self.timeout)
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        if status != 200:
            return Result(DOWN, f"/health answered {status}", ms)
        try:
            rstatus, rbody, _ = http_get(self.backend + "/ready", self.timeout)
            if rstatus != 200:
                return Result(DEGRADED, f"/ready {rstatus}: {rbody[:120].decode('utf-8', 'replace')}", ms)
        except (urllib.error.URLError, OSError):
            pass
        if ms > self.slow_ms:
            return Result(DEGRADED, f"slow: {ms:.0f} ms", ms)
        return Result(OK, f"{ms:.0f} ms", ms)

    def _fetch_components(self):
        with self._components_lock:
            if self._components is None:
                try:
                    status, body, _ = http_get(
                        self.backend + "/dashboard/components", self.timeout + 5, headers=self.dashboard_auth
                    )
                    if status == 401:
                        self._components = ("auth", None)
                    elif status != 200:
                        self._components = ("error", f"/dashboard/components answered {status}")
                    else:
                        self._components = ("ok", json.loads(body).get("components") or {})
                except (urllib.error.URLError, OSError, ValueError) as e:
                    self._components = ("error", short_error(e))
            return self._components

    def component(self, key: str) -> Result:
        if not self.backend:
            return Result(OFF, "no backend URL")
        kind, data = self._fetch_components()
        if kind == "auth":
            return Result(DEGRADED, "dashboard login needed: give omi-health DASHBOARD_USERNAME/PASSWORD")
        if kind == "error":
            return Result(DOWN, f"backend unreachable: {data}")
        item = data.get(key)
        if not item:
            return Result(OFF, "not reported by this backend")
        st = item.get("status")
        detail = str(item.get("detail", ""))[:160]
        if st == "up":
            return Result(OK, detail)
        if st == "not_configured":
            return Result(OFF, detail)
        return Result(DOWN, detail)

    def check_firestore(self) -> Result:
        if not self.firestore:
            return Result(OFF, "")
        try:
            status, body, ms = http_get(self.firestore + "/", self.timeout)
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        if status == 200:
            return Result(DEGRADED if ms > self.slow_ms else OK, f"{ms:.0f} ms", ms)
        return Result(DOWN, f"answered {status}", ms)

    def check_redis(self) -> Result:
        if not self.redis:
            return Result(OFF, "")
        host, _, port = self.redis.rpartition(":")
        started = time.monotonic()
        try:
            with socket.create_connection((host, int(port)), timeout=self.timeout) as s:
                if self.redis_password:
                    s.sendall(f"AUTH {self.redis_password}\r\n".encode())
                    s.recv(64)
                s.sendall(b"PING\r\n")
                reply = s.recv(64)
        except (OSError, ValueError) as e:
            return Result(DOWN, short_error(e))
        ms = (time.monotonic() - started) * 1000
        if reply.startswith(b"+PONG"):
            return Result(OK, f"PONG in {ms:.0f} ms", ms)
        return Result(DOWN, f"unexpected reply {reply[:40]!r}", ms)

    def check_caldav(self) -> Result:
        if not self.caldav:
            return Result(OFF, "bundled CalDAV off")
        password = env("CALDAV_PASSWORD")
        if not password:
            try:
                with open(self.caldav_password_file) as f:
                    password = f.read().strip()
            except OSError:
                password = ""
        headers = {"Depth": "0", "Content-Type": "application/xml", **basic_auth(self.caldav_user, password)}
        url = urllib.parse.urljoin(self.caldav, "/.well-known/caldav")
        body = b'<?xml version="1.0"?><propfind xmlns="DAV:"><prop><current-user-principal/></prop></propfind>'
        try:
            status, _, ms = http_get(url, self.timeout, headers=headers, method="PROPFIND", body=body)
            if status in (301, 302, 307, 308):
                status, _, ms = http_get(self.caldav + "/", self.timeout, headers=headers, method="PROPFIND", body=body)
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        if status == 207:
            return Result(OK, f"PROPFIND 207 in {ms:.0f} ms", ms)
        if status == 401:
            if password:
                return Result(DEGRADED, "answers, but rejected the CalDAV password", ms)
            return Result(OK, "answers (no password here to log in with)", ms)
        return Result(DOWN, f"PROPFIND answered {status}", ms)

    def check_audio(self) -> Result:
        if not self.audio:
            return Result(OFF, "OMI_AUDIO_URL=off")
        try:
            status, body, ms = http_get(self.audio + "/health", self.timeout)
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        try:
            info = json.loads(body)
        except ValueError:
            info = {}
        engine = info.get("asr_engine", "?")
        if status == 503 or info.get("ready") is False:
            return Result(DEGRADED, f"loading models ({engine})", ms)
        if status != 200:
            return Result(DOWN, f"/health answered {status}", ms)
        in_flight = info.get("asr_in_flight")
        detail = f"{engine}, {ms:.0f} ms"
        if in_flight is not None:
            detail += f", {in_flight} transcription(s) in flight"
            if in_flight >= self.audio_max_in_flight:
                return Result(DEGRADED, f"queue backing up: {detail}", ms)
        return Result(OK, detail, ms)

    def check_llm(self) -> Result:
        """The merge proxy's /health asks every upstream model server live; else /v1/models."""
        if not self.llm:
            return Result(OFF, "")
        try:
            status, body, ms = http_get(self.llm + "/health", self.timeout + 10)
            info = json.loads(body) if status == 200 else {}
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        except ValueError:
            info = {}
        upstreams = info.get("upstreams") if isinstance(info, dict) else None
        if isinstance(upstreams, dict) and upstreams:
            down = sorted(tag for tag, st in upstreams.items() if st != "up")
            count = info.get("model_count", 0)
            detail = f"{len(upstreams) - len(down)}/{len(upstreams)} model servers up, {count} models, {ms:.0f} ms"
            if len(down) == len(upstreams):
                return Result(DOWN, f"no model server answers ({', '.join(down)})", ms)
            if down:
                return Result(DEGRADED, f"{detail}; down: {', '.join(down)}", ms)
            if not count:
                return Result(DEGRADED, f"{detail}; no models listed", ms)
            if ms > self.llm_slow_ms:
                return Result(DEGRADED, f"slow: {detail}", ms)
            return Result(OK, detail, ms)
        # Not the merge proxy (e.g. llama.cpp or Argos directly): its model list.
        try:
            status, body, ms = http_get(self.llm + "/v1/models", self.timeout + 10)
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        if status != 200:
            return Result(DOWN, f"/v1/models answered {status}", ms)
        try:
            models = json.loads(body).get("data") or []
        except ValueError:
            models = []
        if not models:
            return Result(DEGRADED, f"no models listed ({ms:.0f} ms)", ms)
        if ms > self.llm_slow_ms:
            return Result(DEGRADED, f"{len(models)} models, slow: {ms:.0f} ms", ms)
        return Result(OK, f"{len(models)} models, {ms:.0f} ms", ms)

    def check_adapter(self) -> Result:
        if not self.adapter:
            return Result(OFF, "")
        try:
            status, _, ms = http_get(self.adapter + "/health", self.timeout)
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        return Result(OK if status == 200 else DOWN, f"{status}, {ms:.0f} ms", ms)

    def check_hermes(self) -> Result:
        if not self.hermes:
            return Result(OFF, "HERMES_API_URL not set")
        headers = {"Authorization": f"Bearer {self.hermes_key}"} if self.hermes_key else {}
        try:
            status, _, ms = http_get(self.hermes + "/health", self.timeout, headers=headers)
        except (urllib.error.URLError, OSError) as e:
            return Result(DOWN, short_error(e))
        if status == 200:
            return Result(DEGRADED if ms > self.slow_ms else OK, f"{ms:.0f} ms", ms)
        return Result(DOWN, f"/health answered {status}", ms)

    def check_disk(self, path: str) -> Result:
        if not os.path.isdir(path):
            return Result(OFF, f"{path} not mounted here")
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if not total:
            return Result(OFF, "no size")
        pct = free * 100 / total
        detail = f"{free / 1e9:.1f} GB free of {total / 1e9:.0f} GB ({pct:.0f}%)"
        if pct < self.disk_crit:
            return Result(DOWN, detail)
        if pct < self.disk_warn:
            return Result(DEGRADED, detail)
        return Result(OK, detail)

    def check_gpu(self) -> Result:
        if shutil.which("nvidia-smi"):
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout + 5,
                )
            except (OSError, subprocess.SubprocessError) as e:
                return Result(DOWN, short_error(e))
            if out.returncode != 0:
                return Result(DOWN, (out.stderr or out.stdout).strip()[:160])
            worst, parts = 0.0, []
            for line in out.stdout.strip().splitlines():
                name, used, total = [x.strip() for x in line.split(",")]
                pct = float(used) * 100 / max(float(total), 1)
                worst = max(worst, pct)
                parts.append(f"{name}: {float(used) / 1024:.1f}/{float(total) / 1024:.0f} GB")
            return Result(DEGRADED if worst >= self.gpu_warn else OK, "; ".join(parts))
        return Result(OFF, "no nvidia-smi here")

    def check_backup(self) -> Result:
        if not self.backup_dir or not os.path.isdir(self.backup_dir):
            return Result(OFF, "no backup directory mounted")
        newest = None
        for name in os.listdir(self.backup_dir):
            if name.startswith("omi-backup-") and name.endswith(".json"):
                try:
                    with open(os.path.join(self.backup_dir, name)) as f:
                        meta = json.load(f)
                    created = dt.datetime.strptime(meta["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=dt.timezone.utc
                    )
                except (OSError, ValueError, KeyError):
                    continue
                if os.path.exists(os.path.join(self.backup_dir, meta.get("archive", ""))) and (
                    newest is None or created > newest[0]
                ):
                    newest = (created, meta)
        if newest is None:
            return Result(DOWN, "no backup found yet")
        age_h = (dt.datetime.now(dt.timezone.utc) - newest[0]).total_seconds() / 3600
        meta = newest[1]
        size = meta.get("archive_bytes", 0)
        size_text = f"{size / 1e6:.0f} MB" if size >= 1e6 else f"{size / 1e3:.0f} KB"
        detail = f"{fmt_duration(age_h * 3600)} ago, {size_text}"
        verify = os.path.join(self.backup_dir, "verify", "latest.json")
        verify_note = ""
        if os.path.isfile(verify):
            try:
                with open(verify) as f:
                    v = json.load(f)
                verify_note = f"; last test restore {'OK' if v.get('ok') else 'FAILED'} ({v.get('finished_at', '?')[:10]})"
                if not v.get("ok"):
                    return Result(DEGRADED, detail + verify_note)
            except (OSError, ValueError):
                pass
        if age_h > 2 * self.backup_max_age_h:
            return Result(DOWN, "too old: " + detail + verify_note)
        if age_h > self.backup_max_age_h or meta.get("warnings"):
            extra = f", {len(meta['warnings'])} warning(s)" if meta.get("warnings") else ""
            return Result(DEGRADED, detail + extra + verify_note)
        return Result(OK, detail + verify_note)


# ----------------------------------------------------------------------------- watchdog


class Watchdog:
    def __init__(self, checks: Checks | None = None, notifiers: list[Notifier] | None = None):
        self.checks = checks or Checks()
        self.notifiers = notifiers if notifiers is not None else notifiers_from_env()
        self.host = env("OMI_HEALTH_NAME", socket.gethostname())
        self.status_url = env("OMI_HEALTH_PUBLIC_URL")
        self.interval = env_float("OMI_HEALTH_INTERVAL_S", 60)
        fail_after = int(env_float("OMI_HEALTH_FAIL_AFTER", 2))
        recover_after = int(env_float("OMI_HEALTH_RECOVER_AFTER", 1))
        cooldown = env_float("OMI_HEALTH_COOLDOWN_S", 900)
        self.items = self.checks.all()
        self.trackers = {
            name: Tracker(name, label, fail_after, recover_after, cooldown) for name, label, _ in self.items
        }
        self.state_file = env("OMI_HEALTH_STATE_FILE")
        self.digest_at = env("OMI_HEALTH_DIGEST_AT")
        self.last_digest_day = None
        self.deliveries: list[dict] = []
        self.lock = threading.Lock()
        self.last_round = 0.0
        self._load()

    def _load(self) -> None:
        if not self.state_file or not os.path.isfile(self.state_file):
            return
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            for name, saved in (data.get("trackers") or {}).items():
                if name in self.trackers:
                    self.trackers[name].load(saved)
            self.last_digest_day = data.get("last_digest_day")
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        if not self.state_file:
            return
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(
                    {"trackers": {n: t.dump() for n, t in self.trackers.items()}, "last_digest_day": self.last_digest_day},
                    f,
                )
            os.replace(tmp, self.state_file)
        except OSError as e:
            log(f"could not save state: {e}")

    def run_round(self, now: float | None = None) -> list[Event]:
        self.checks.start_round()
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fn): name for name, _, fn in self.items}
            for fut in concurrent.futures.as_completed(futures):
                name = futures[fut]
                try:
                    results[name] = fut.result()
                except Exception as e:  # noqa: BLE001 - one broken check must not stop the rest
                    results[name] = Result(DOWN, f"check crashed: {short_error(e)}")
        now = now or time.time()
        events = []
        with self.lock:
            for name, _, _ in self.items:
                events += self.trackers[name].observe(results[name], now)
            self.last_round = now
        for e in events:
            self.dispatch(e.kind, e.title(self.host), e.message(), e.as_dict(self.host, self.status_url))
        self.maybe_digest(now)
        self._save()
        return events

    def run_once(self) -> None:
        """One round for `--once`: report what this round found, with no side effects.

        A lone round cannot meet OMI_HEALTH_FAIL_AFTER (the streak lives only in the daemon's
        memory), so it is judged on its own; and it neither alerts nor rewrites the state file,
        which the running daemon owns.
        """
        for tracker in self.trackers.values():
            tracker.fail_after = 1
        self.notifiers, self.state_file = [], ""
        self.run_round()

    def dispatch(self, kind: str, title: str, message: str, payload: dict) -> None:
        log(f"alert {kind}: {title}: {message}")
        for n in self.notifiers:
            entry = {"at": iso(time.time()), "via": n.name, "title": title, "ok": True}
            try:
                n.send(kind, title, message, payload)
            except Exception as e:  # noqa: BLE001
                entry.update(ok=False, error=str(e)[:200])
                log(f"delivery via {n.name} failed: {e}")
            with self.lock:
                self.deliveries = (self.deliveries + [entry])[-20:]

    def maybe_digest(self, now: float) -> None:
        if not self.digest_at:
            return
        try:
            hh, mm = (int(x) for x in self.digest_at.split(":"))
        except ValueError:
            return
        local = dt.datetime.fromtimestamp(now)
        if (local.hour, local.minute) < (hh, mm) or self.last_digest_day == local.date().isoformat():
            return
        self.last_digest_day = local.date().isoformat()
        title, message = self.digest_text()
        payload = {"source": "omi-health", "event": "digest", "title": title, "message": message, "host": self.host}
        self.dispatch("digest", title, message, payload)

    def digest_text(self) -> tuple[str, str]:
        with self.lock:
            active = [t for t in self.trackers.values() if t.state != OFF]
            problems = [t for t in active if t.state != OK]
        if not problems:
            return f"[{self.host}] all good", f"All {len(active)} checks ok."
        lines = [f"{t.label}: {t.state} ({t.detail})" for t in problems]
        return f"[{self.host}] {len(problems)} problem(s)", "\n".join(lines)

    def overall(self) -> str:
        states = [t.state for t in self.trackers.values() if t.state != OFF]
        if DOWN in states:
            return DOWN
        if DEGRADED in states:
            return DEGRADED
        return OK if states else OFF

    def status(self) -> dict:
        with self.lock:
            return {
                "host": self.host,
                "overall": self.overall(),
                "generated_at": iso(time.time()),
                "last_round": iso(self.last_round) if self.last_round else None,
                "interval_s": self.interval,
                "checks": [self.trackers[name].snapshot() for name, _, _ in self.items],
                "alerts": {
                    "channels": [n.name for n in self.notifiers],
                    "recent": list(reversed(self.deliveries)),
                },
            }

    def loop(self) -> None:
        while True:
            started = time.time()
            try:
                self.run_round()
            except Exception as e:  # noqa: BLE001
                log(f"round failed: {e}")
            time.sleep(max(1.0, self.interval - (time.time() - started)))


# ----------------------------------------------------------------------------- status page

PAGE_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#5d6675;--line:#e3e6eb;
--ok:#1f7a3d;--ok-bg:#e3f4e8;--warn:#8a5a00;--warn-bg:#fdf1d8;--bad:#b42318;--bad-bg:#fde6e4;--off:#6b7280;--off-bg:#eceef1}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1f26;--text:#e7eaee;--muted:#9aa3ae;--line:#2a313a;
--ok:#6fd08c;--ok-bg:#16301f;--warn:#f2c46b;--warn-bg:#352a12;--bad:#ff8a80;--bad-bg:#3a1a18;--off:#9aa3ae;--off-bg:#242a32}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:880px;margin:0 auto;padding:24px 16px 40px}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:20px;margin:0;font-weight:650}
.sub{color:var(--muted);font-size:13px}
.banner{border-radius:12px;padding:14px 16px;margin-bottom:16px;font-weight:600}
.banner.ok{background:var(--ok-bg);color:var(--ok)}.banner.degraded{background:var(--warn-bg);color:var(--warn)}
.banner.down{background:var(--bad-bg);color:var(--bad)}.banner.off{background:var(--off-bg);color:var(--off)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.row{display:grid;grid-template-columns:1fr auto;gap:4px 12px;padding:12px 16px;border-top:1px solid var(--line)}
.row:first-child{border-top:0}
.name{font-weight:600}.detail{grid-column:1/-1;color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.pill{font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.03em;align-self:start}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.degraded{background:var(--warn-bg);color:var(--warn)}
.pill.down{background:var(--bad-bg);color:var(--bad)}.pill.off{background:var(--off-bg);color:var(--off)}
h2{font-size:15px;margin:24px 0 8px}
ul{margin:0;padding-left:18px;color:var(--muted);font-size:13px}
a{color:inherit}
"""

BANNER = {OK: "All systems ok", DEGRADED: "Something needs a look", DOWN: "Something is down", OFF: "No checks yet"}


def render_page(status: dict) -> str:
    rows = []
    order = sorted(status["checks"], key=lambda c: (-SEVERITY[c["state"]], 0))
    for c in order:
        since = c["since"].replace("T", " ").replace("Z", " UTC")
        # Most details already carry the response time; add it only where they do not.
        has_ms = " ms" in (c["detail"] or "")
        latency = f" · {c['latency_ms']:.0f} ms" if c.get("latency_ms") is not None and not has_ms else ""
        state_note = f" · since {since}" if c["state"] != OFF else ""
        rows.append(
            f'<div class="row"><div class="name">{html.escape(c["label"])}</div>'
            f'<span class="pill {c["state"]}">{c["state"]}</span>'
            f'<div class="detail">{html.escape(c["detail"] or "")}{latency}{html.escape(state_note)}</div></div>'
        )
    channels = ", ".join(status["alerts"]["channels"]) or "none configured (HEALTH.md)"
    recent = "".join(
        f"<li>{html.escape(d['at'])} via {html.escape(d['via'])}: {html.escape(d['title'])}"
        f"{'' if d['ok'] else ' — FAILED: ' + html.escape(d.get('error', ''))}</li>"
        for d in status["alerts"]["recent"][:8]
    ) or "<li>none yet</li>"
    overall = status["overall"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Omi health</title>
<style>{PAGE_CSS}</style></head>
<body><main>
<header><h1>Omi health · {html.escape(status["host"])}</h1>
<span class="sub">checked {html.escape((status["last_round"] or "not yet").replace("T", " ").replace("Z", " UTC"))}
 · every {status["interval_s"]:.0f} s · <a href="status.json">status.json</a></span></header>
<div class="banner {overall}">{BANNER[overall]}</div>
<div class="card">{"".join(rows)}</div>
<h2>Alerts</h2>
<ul><li>Channels: {html.escape(channels)}</li>{recent}</ul>
</main></body></html>
"""


def make_handler(watchdog: Watchdog):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, render_page(watchdog.status()).encode(), "text/html; charset=utf-8")
            elif path == "/status.json":
                self._send(200, json.dumps(watchdog.status(), indent=2).encode(), "application/json")
            elif path == "/healthz":
                self._send(200, b"ok\n", "text/plain")
            else:
                self._send(404, b"not found\n", "text/plain")

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def serve(watchdog: Watchdog, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(watchdog))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="omi-health watchdog (HEALTH.md)")
    parser.add_argument("--once", action="store_true", help="one round, print status.json, exit 1 if anything is down")
    parser.add_argument("--test-alert", action="store_true", help="send a test message to every configured channel")
    args = parser.parse_args(argv)
    watchdog = Watchdog()
    if args.test_alert:
        if not watchdog.notifiers:
            print("no alert channel configured (OMI_HEALTH_NTFY_*, OMI_HEALTH_GOTIFY_*, OMI_HEALTH_WEBHOOK_URL)")
            return 1
        title = f"[{watchdog.host}] omi-health test"
        watchdog.dispatch("test", title, "If you can read this, alerts reach you.", {"source": "omi-health", "event": "test", "title": title})
        return 0 if all(d["ok"] for d in watchdog.deliveries) else 1
    if args.once:
        watchdog.run_once()
        print(json.dumps(watchdog.status(), indent=2))
        return 1 if watchdog.overall() == DOWN else 0
    bind = env("OMI_HEALTH_BIND", "0.0.0.0")
    port = int(env_float("OMI_HEALTH_PORT", 8099))
    serve(watchdog, bind, port)
    log(
        f"status page on {bind}:{port}, checking every {watchdog.interval:.0f} s, alerts via "
        f"{', '.join(n.name for n in watchdog.notifiers) or 'nothing (no channel configured)'}"
    )
    watchdog.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
