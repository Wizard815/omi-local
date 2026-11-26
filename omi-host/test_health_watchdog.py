#!/usr/bin/env python3
"""health/watchdog.py: the alert state machine, the alert formats, the checks, the page.

What this pins down:

  * a check alerts on state CHANGES only: one bad result is not an alert
    (OMI_HEALTH_FAIL_AFTER), a confirmed problem is, the same state again is not,
  * "recovered" is sent at once, names the earlier state and how long it lasted, and
    only for a problem that was announced,
  * a flapping check sends at most one problem alert per cooldown; a problem still there
    when the cooldown ends is sent then; getting worse (degraded -> down) is sent at once,
  * a check that is not configured (off) never alerts,
  * ntfy, Gotify and webhook requests carry what each server expects (checked against a
    local HTTP server standing in for all three), and a failed delivery is retried and
    shown on the status page instead of stopping the watchdog,
  * the checks read the real protocols: HTTP health, Redis PING, CalDAV PROPFIND,
    the merge proxy's upstreams, disk free thresholds and the last backup's manifest,
  * the status page (HTML + /status.json) shows every check and needs no external asset,
  * the daily digest is off by default and sent once a day when set.

Run:  python3 test_health_watchdog.py     (no pytest required)
"""

import contextlib
import datetime as dt
import json
import os
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "health"))
os.environ["OMI_HEALTH_FAST_RETRY"] = "1"
import watchdog as wd  # noqa: E402

OK, DEGRADED, DOWN, OFF = wd.OK, wd.DEGRADED, wd.DOWN, wd.OFF
ROOTS = []


def R(state, detail=""):
    return wd.Result(state, detail)


def feed(tracker, states, start=1000.0, step=60.0):
    """Observe a sequence of raw states one interval apart; return [(t, kind, previous)]."""
    out = []
    for i, st in enumerate(states):
        now = start + i * step
        for e in tracker.observe(R(st, f"r{i}"), now):
            out.append((now, e.kind, e.previous))
    return out


# ----------------------------------------------------------------------------- state machine


def test_one_bad_result_is_not_an_alert_and_a_confirmed_one_is():
    t = wd.Tracker("redis", "Redis", fail_after=2, cooldown_s=900)
    assert feed(t, [OK, DOWN, OK, OK]) == [], "a blip"
    t = wd.Tracker("redis", "Redis", fail_after=2, cooldown_s=900)
    events = feed(t, [OK, DOWN, DOWN, DOWN, DOWN])
    assert events == [(1120.0, "down", OK)], events
    assert t.state == DOWN


def test_recovered_is_immediate_and_says_how_long():
    t = wd.Tracker("audio", "Audio", fail_after=2, cooldown_s=900)
    events = []
    for i, st in enumerate([OK, DOWN, DOWN, DOWN, OK]):
        events += t.observe(R(st, "fine" if st == OK else "refused"), 1000 + i * 60)
    kinds = [e.kind for e in events]
    assert kinds == ["down", "recovered"], kinds
    rec = events[-1]
    assert rec.previous == DOWN and rec.state == OK
    assert rec.duration_s == 180, "from the first confirmed down (t=1060) to t=1240"
    assert "after 3 min" in rec.message() and rec.title("omi") == "[omi] Audio recovered", rec.message()


def test_a_problem_never_announced_does_not_send_recovered():
    t = wd.Tracker("llm", "LLM", fail_after=3, cooldown_s=900)
    assert feed(t, [OK, DOWN, DOWN, OK, OK]) == []


def test_flapping_is_held_to_one_problem_alert_per_cooldown():
    t = wd.Tracker("backend", "Backend", fail_after=1, cooldown_s=600)
    # down / ok / down / ok ... every minute for 10 minutes, then stays down.
    seq = [OK] + [DOWN, OK] * 5 + [DOWN] * 6
    events = feed(t, seq)
    kinds = [(int(at - 1000), k) for at, k, _ in events]
    # t=60 down, t=120 recovered; the next downs inside the 600 s cooldown are held,
    # and the one still standing when it ends (t=660) is sent.
    assert kinds == [(60, "down"), (120, "recovered"), (660, "down")], kinds


def test_getting_worse_skips_the_cooldown_getting_better_waits():
    t = wd.Tracker("llm", "LLM", fail_after=1, cooldown_s=900)
    events = feed(t, [OK, DEGRADED, DOWN, DEGRADED, DEGRADED])
    assert [(k, p) for _, k, p in events] == [("degraded", OK), ("down", DEGRADED)], events
    # down -> degraded is held until the cooldown since the last problem alert ends
    later = t.observe(R(DEGRADED), 1120 + 900)
    assert [(e.kind, e.previous) for e in later] == [("degraded", DOWN)]


def test_off_checks_never_alert():
    t = wd.Tracker("hermes", "Hermes", fail_after=1)
    assert feed(t, [OFF, OFF, OFF]) == []
    t = wd.Tracker("gpu", "GPU", fail_after=1)
    assert [k for _, k, _ in feed(t, [OK, DOWN, OFF, OFF])] == ["down"], "turning a check off is not 'recovered'"


def test_a_problem_at_start_still_needs_confirming_then_alerts():
    t = wd.Tracker("caldav", "CalDAV", fail_after=2)
    assert [k for _, k, _ in feed(t, [DOWN, DOWN])] == ["down"]


def test_state_survives_a_watchdog_restart():
    t = wd.Tracker("redis", "Redis", fail_after=1)
    feed(t, [OK, DOWN])
    saved = json.loads(json.dumps(t.dump()))
    t2 = wd.Tracker("redis", "Redis", fail_after=1)
    t2.load(saved)
    assert [e.kind for e in t2.observe(R(DOWN), 5000)] == [], "already announced before the restart"
    assert [e.kind for e in t2.observe(R(OK), 5060)] == ["recovered"]


# ----------------------------------------------------------------------------- notifier formats


class Capture(BaseHTTPRequestHandler):
    requests = []
    fail_next = 0

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if Capture.fail_next:
            Capture.fail_next -= 1
            self.send_response(502)
            self.end_headers()
            return
        Capture.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"id":1}')

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def capture_server():
    Capture.requests, Capture.fail_next = [], 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def down_event():
    return wd.Event("down", "redis", "Redis", DOWN, OK, "connection refused", 1000.0, 1120.0)


def test_ntfy_gotify_webhook_requests():
    ev = down_event()
    payload = ev.as_dict("omi", "http://192.0.2.10:8099/")
    with capture_server() as base:
        wd.Ntfy(base, "omi-alerts", token="tk_abc", click="http://192.0.2.10:8099/").send(
            "down", ev.title("omi"), ev.message(), payload
        )
        wd.Gotify(base + "/", "AppToken1", click="http://192.0.2.10:8099/").send("recovered", "t", "m", payload)
        wd.Webhook(base + "/hook", token="whk").send("down", ev.title("omi"), ev.message(), payload)
        ntfy, gotify, hook = Capture.requests
    assert ntfy["path"] == "/omi-alerts"
    h = ntfy["headers"]
    assert h["Title"] == "[omi] Redis DOWN" and h["Priority"] == "5" and h["Tags"] == "rotating_light"
    assert h["Authorization"] == "Bearer tk_abc" and h["Click"] == "http://192.0.2.10:8099/"
    assert ntfy["body"].decode() == "Redis is down: connection refused"

    assert gotify["path"] == "/message" and gotify["headers"]["X-Gotify-Key"] == "AppToken1"
    g = json.loads(gotify["body"])
    assert g["title"] == "t" and g["message"] == "m" and g["priority"] == 4
    assert g["extras"]["client::notification"]["click"]["url"] == "http://192.0.2.10:8099/"

    assert hook["path"] == "/hook" and hook["headers"]["Authorization"] == "Bearer whk"
    body = json.loads(hook["body"])
    assert body["event"] == "down" and body["check"] == "redis" and body["state"] == DOWN
    assert body["previous"] == OK and body["duration_s"] == 120 and body["since"] == "1970-01-01T00:16:40Z"
    assert body["source"] == "omi-health" and body["status_url"] == "http://192.0.2.10:8099/"


def test_notifiers_from_env_only_configured_ones():
    keys = ["OMI_HEALTH_NTFY_URL", "OMI_HEALTH_NTFY_TOPIC", "OMI_HEALTH_GOTIFY_URL", "OMI_HEALTH_GOTIFY_TOKEN", "OMI_HEALTH_WEBHOOK_URL"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        assert wd.notifiers_from_env() == [], "nothing is sent anywhere by default"
        os.environ["OMI_HEALTH_NTFY_URL"] = "http://192.0.2.30"
        assert wd.notifiers_from_env() == [], "a URL without a topic is not enough"
        os.environ["OMI_HEALTH_NTFY_TOPIC"] = "t"
        os.environ["OMI_HEALTH_WEBHOOK_URL"] = "http://192.0.2.32/h"
        assert [n.name for n in wd.notifiers_from_env()] == ["ntfy", "webhook"]
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


# ----------------------------------------------------------------------------- watchdog loop


class FakeChecks:
    def __init__(self):
        self.states = {"backend": OK, "redis": OK, "hermes": OFF}

    def all(self):
        return [(n, n.title(), (lambda n=n: R(self.states[n], f"{n} is {self.states[n]}"))) for n in self.states]

    def start_round(self):
        pass


def test_watchdog_round_delivers_retries_and_reports_failures():
    with capture_server() as base:
        os.environ["OMI_HEALTH_FAIL_AFTER"] = "1"
        try:
            checks = FakeChecks()
            dog = wd.Watchdog(checks=checks, notifiers=[wd.Ntfy(base, "omi"), wd.Webhook(base + "/hook")])
        finally:
            os.environ.pop("OMI_HEALTH_FAIL_AFTER")
        dog.host = "omi"
        assert dog.run_round(1000) == []
        checks.states["redis"] = DOWN
        Capture.fail_next = 1  # the first POST fails once and is retried
        events = dog.run_round(1060)
        assert [e.kind for e in events] == ["down"]
        assert [r["path"] for r in Capture.requests] == ["/omi", "/hook"], Capture.requests
        st = dog.status()
        assert st["overall"] == DOWN and {c["name"]: c["state"] for c in st["checks"]}["hermes"] == OFF
        assert [d["ok"] for d in st["alerts"]["recent"]] == [True, True]

    # The channel is gone now: the watchdog keeps going and shows the failure.
    checks.states["redis"] = OK
    events = dog.run_round(1120)
    assert [e.kind for e in events] == ["recovered"]
    recent = dog.status()["alerts"]["recent"]
    assert recent[0]["ok"] is False and "error" in recent[0], recent[0]
    page = wd.render_page(dog.status())
    assert "FAILED" in page


def test_once_reports_this_rounds_problem_without_alerting_or_saving():
    # `watchdog.py --once` must exit 1 when something is down now (HEALTH.md), though a lone
    # round cannot meet OMI_HEALTH_FAIL_AFTER, and must leave the daemon's alerts and state alone.
    root = tempfile.mkdtemp()
    ROOTS.append(root)
    state_file = os.path.join(root, "state.json")
    os.environ["OMI_HEALTH_STATE_FILE"] = state_file
    try:
        daemon = wd.Watchdog(checks=FakeChecks(), notifiers=[])
        daemon.run_round(1000)
        saved = open(state_file).read()
        with capture_server() as base:
            checks = FakeChecks()
            checks.states["redis"] = DOWN
            once = wd.Watchdog(checks=checks, notifiers=[wd.Webhook(base + "/hook")])
            once.run_once()
            assert Capture.requests == [], Capture.requests
    finally:
        os.environ.pop("OMI_HEALTH_STATE_FILE")
    st = once.status()
    assert st["overall"] == DOWN, st
    assert {c["name"]: c["state"] for c in st["checks"]}["redis"] == DOWN
    assert open(state_file).read() == saved


def test_digest_is_off_by_default_and_once_a_day():
    dog = wd.Watchdog(checks=FakeChecks(), notifiers=[])
    sent = []
    dog.dispatch = lambda kind, title, message, payload: sent.append((kind, title, message))
    dog.run_round(time.time())
    assert sent == []
    dog.digest_at = "00:00"
    dog.run_round(time.time())
    dog.run_round(time.time() + 1)
    assert len(sent) == 1 and sent[0][0] == "digest" and "All 2 checks ok" in sent[0][2], sent


# ----------------------------------------------------------------------------- checks against fakes


class Fake(BaseHTTPRequestHandler):
    routes = {}

    def _route(self):
        status, body, headers = Fake.routes.get((self.command, self.path), (404, b"", {}))
        if callable(body):
            status, body = body(self)
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_PROPFIND = do_POST = _route  # noqa: N815

    def log_message(self, *a):
        pass


class FakeRedis(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(64)
        if data.startswith(b"PING"):
            self.request.sendall(b"+PONG\r\n")


def make_checks(**env):
    base = {
        "OMI_HEALTH_DISKS": "off",
        "OMI_HEALTH_BACKUP_DIR": "off",
        "OMI_HEALTH_LLM_ADAPTER_URL": "off",
        "HERMES_API_URL": "off",
        "OMI_HEALTH_TIMEOUT_S": "2",
    }
    base.update(env)
    saved = {k: os.environ.get(k) for k in base}
    os.environ.update(base)
    try:
        return wd.Checks()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_checks_speak_each_protocol():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    redis = socketserver.TCPServer(("127.0.0.1", 0), FakeRedis)
    threading.Thread(target=redis.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    tmp = tempfile.mkdtemp()
    ROOTS.append(tmp)
    pw = os.path.join(tmp, "caldav_password")
    open(pw, "w").write("pw1\n")

    def caldav(handler):
        ok = handler.headers.get("Authorization") == wd.basic_auth("omi", "pw1")["Authorization"]
        return (207, b"<multistatus/>") if ok else (401, b"")

    Fake.routes = {
        ("GET", "/health"): (200, b'{"status":"ok"}', {}),
        ("GET", "/ready"): (200, b"{}", {}),
        ("GET", "/dashboard/components"): (
            200,
            json.dumps({"components": {"auth_emulator": {"status": "down", "detail": "connection refused"},
                                       "vector_db": {"status": "up", "detail": "ChromaDB (12 vectors)"}}}).encode(),
            {},
        ),
        ("PROPFIND", "/.well-known/caldav"): (0, caldav, {}),
        ("GET", "/audio/health"): (200, b'{"status":"healthy","ready":true,"asr_engine":"whispercpp","asr_in_flight":5}', {}),
        ("GET", "/proxy/health"): (200, b'{"ok":true,"upstreams":{"llama":"up","or":"timed out"},"model_count":3}', {}),
    }
    try:
        c = make_checks(
            OMI_HEALTH_BACKEND_URL=base,
            OMI_HEALTH_FIRESTORE_URL="off",
            OMI_HEALTH_REDIS=f"127.0.0.1:{redis.server_address[1]}",
            OMI_HEALTH_CALDAV_URL=base + "/",
            OMI_HEALTH_CALDAV_PASSWORD_FILE=pw,
            OMI_HEALTH_AUDIO_URL=base + "/audio",
            OMI_HEALTH_LLM_URL=base + "/proxy",
        )
        assert c.check_backend().state == OK
        assert c.check_redis().state == OK and "PONG" in c.check_redis().detail
        assert c.component("auth_emulator").state == DOWN
        assert c.component("vector_db").state == OK and "12 vectors" in c.component("vector_db").detail
        r = c.check_caldav()
        assert r.state == OK and "207" in r.detail, r
        open(pw, "w").write("wrong")
        assert c.check_caldav().state == DEGRADED
        r = c.check_audio()
        assert r.state == DEGRADED and "5 transcription(s) in flight" in r.detail, r
        r = c.check_llm()
        assert r.state == DEGRADED and "1/2 model servers up" in r.detail and "or" in r.detail, r
        assert c.check_firestore().state == OFF and c.check_hermes().state == OFF and c.check_adapter().state == OFF

        Fake.routes[("GET", "/ready")] = (503, b'{"status":"not_ready","redis":{"status":"unavailable"}}', {})
        assert c.check_backend().state == DEGRADED
        Fake.routes[("GET", "/dashboard/components")] = (401, b"", {})
        c.start_round()
        assert "DASHBOARD_USERNAME" in c.component("auth_emulator").detail
    finally:
        srv.shutdown()
        srv.server_close()
        redis.shutdown()
        redis.server_close()
    c.start_round()
    assert c.check_backend().state == DOWN
    assert c.check_redis().state == DOWN
    assert c.component("vector_db").state == DOWN


def test_disk_thresholds():
    c = make_checks(OMI_HEALTH_DISK_WARN_PCT="15", OMI_HEALTH_DISK_CRIT_PCT="5")
    real = os.statvfs

    class V:
        f_frsize, f_blocks = 4096, 1000

    try:
        for avail, want in ((500, OK), (100, DEGRADED), (20, DOWN)):
            V.f_bavail = avail
            os.statvfs = lambda p: V
            assert c.check_disk(HERE).state == want, (avail, want)
    finally:
        os.statvfs = real
    assert c.check_disk("/nonexistent-mount").state == OFF


def test_last_backup_age_and_verify_result():
    tmp = tempfile.mkdtemp()
    ROOTS.append(tmp)
    c = make_checks(OMI_HEALTH_BACKUP_DIR=tmp, OMI_HEALTH_BACKUP_MAX_AGE_H="26")
    assert c.check_backup().state == DOWN and "no backup" in c.check_backup().detail

    def put(hours_ago, warnings=()):
        t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
        name = "omi-backup-" + t.strftime("%Y%m%dT%H%M%SZ")
        open(os.path.join(tmp, name + ".tar.gz"), "w").write("x")
        json.dump(
            {"created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "archive": name + ".tar.gz", "archive_bytes": 5e6,
             "warnings": list(warnings)},
            open(os.path.join(tmp, name + ".json"), "w"),
        )

    put(60)
    assert c.check_backup().state == DOWN, "older than twice the limit"
    put(30)
    assert c.check_backup().state == DEGRADED
    put(2)
    r = c.check_backup()
    assert r.state == OK and "2.0 h ago" in r.detail, r
    os.makedirs(os.path.join(tmp, "verify"))
    json.dump({"ok": False, "finished_at": "2026-11-01T04:00:00Z"}, open(os.path.join(tmp, "verify", "latest.json"), "w"))
    r = c.check_backup()
    assert r.state == DEGRADED and "test restore FAILED" in r.detail, r


# ----------------------------------------------------------------------------- page


def test_status_page_and_json_are_served_with_no_external_assets():
    dog = wd.Watchdog(checks=FakeChecks(), notifiers=[])
    dog.host = "omi"
    dog.run_round(1000)
    srv = wd.serve(dog, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        st = json.loads(urllib.request.urlopen(base + "/status.json").read())
        assert urllib.request.urlopen(base + "/healthz").read() == b"ok\n"
    finally:
        srv.shutdown()
        srv.server_close()
    assert "Omi health · omi" in page and "All systems ok" in page
    assert "prefers-color-scheme:dark" in page
    for needle in ("http://", "https://", "<script", "<link", "src=", "@import", "url("):
        assert needle not in page, needle
    assert st["overall"] == OK and [c["name"] for c in st["checks"]] == ["backend", "redis", "hermes"]


def main() -> None:
    tests = [
        test_one_bad_result_is_not_an_alert_and_a_confirmed_one_is,
        test_recovered_is_immediate_and_says_how_long,
        test_a_problem_never_announced_does_not_send_recovered,
        test_flapping_is_held_to_one_problem_alert_per_cooldown,
        test_getting_worse_skips_the_cooldown_getting_better_waits,
        test_off_checks_never_alert,
        test_a_problem_at_start_still_needs_confirming_then_alerts,
        test_state_survives_a_watchdog_restart,
        test_ntfy_gotify_webhook_requests,
        test_notifiers_from_env_only_configured_ones,
        test_watchdog_round_delivers_retries_and_reports_failures,
        test_once_reports_this_rounds_problem_without_alerting_or_saving,
        test_digest_is_off_by_default_and_once_a_day,
        test_checks_speak_each_protocol,
        test_disk_thresholds,
        test_last_backup_age_and_verify_result,
        test_status_page_and_json_are_served_with_no_external_assets,
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
