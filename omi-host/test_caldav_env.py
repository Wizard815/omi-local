#!/usr/bin/env python3
"""caldav-env.sh: the CalDAV server (Radicale) that runs inside omi-local.

entrypoint.sh sources caldav-env.sh under `set -uo pipefail` and then calls
omi_caldav_start; this runs it the same way, with compose's empty-string
variables, against a scratch data volume.

What this pins down:

  * with no password set, one is generated on first start, owner-only, and the
    same one comes back on every later start (phones stay signed in),
  * the backend's default account is this server, with that login, unless the
    operator points CALDAV_DEFAULT_URL elsewhere (then CALDAV_USER/PASSWORD go
    with it, as compose always passed them),
  * the users file holding the clear password is owner-only,
  * OMI_BUNDLED_CALDAV=0 writes nothing and starts nothing,
  * calendars from the old omi-radicale volume are copied in once, never over
    existing ones,
  * live, when a radicale binary is available (OMI_RADICALE_BIN or
    /opt/radicale/bin/radicale): the server starts, answers the account with
    207 and a wrong password with 401.

Run:  python3 test_caldav_env.py     (no pytest required)
"""

import base64
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "caldav-env.sh")
SHOW = (
    'printf "\\0%s\\0%s\\0%s\\0%s\\0%s" "$CALDAV_USER" "${CALDAV_PASSWORD:-}" '
    '"${CALDAV_DEFAULT_URL:-}" "${CALDAV_DEFAULT_USERNAME:-}" "${CALDAV_DEFAULT_PASSWORD:-}"'
)


def run(state, *, tail="", **env):
    base = {
        "PATH": os.environ["PATH"],
        "STATE": state,
        "CALDAV_PASSWORD": "",
        "CALDAV_DEFAULT_URL": "",
        "OMI_RADICALE_LEGACY": os.path.join(state, "no-legacy"),
    }
    base.update(env)
    proc = subprocess.run(
        ["bash", "-c", 'set -uo pipefail; . "$1"; ' + (tail or SHOW), "_", SCRIPT],
        env=base,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def values(proc):
    user, password, url, default_user, default_password = proc.stdout.split("\0")[1:6]
    return {"user": user, "password": password, "url": url, "default_user": default_user, "default_password": default_password}


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_password_is_generated_once_and_the_backend_uses_this_server():
    with tempfile.TemporaryDirectory() as state:
        first = values(run(state))
        secret = os.path.join(state, "secrets", "caldav_password")
        assert len(first["password"]) >= 24, first
        assert mode(secret) == 0o600, oct(mode(secret))
        assert values(run(state))["password"] == first["password"], "a restart must keep the password"
        assert first["user"] == "omi"
        assert first["url"] == "http://127.0.0.1:5232/"
        assert first["default_user"] == "omi" and first["default_password"] == first["password"]
        users = os.path.join(state, "radicale", "users")
        assert mode(users) == 0o600, oct(mode(users))
        assert open(users).read() == f"omi:{first['password']}\n"
        config = open(os.path.join(state, "radicale", "config")).read()
        assert f"filesystem_folder = {state}/radicale/collections" in config
        assert "hosts = 0.0.0.0:5232" in config and "type = owner_only" in config


def test_the_operators_password_and_server_win():
    with tempfile.TemporaryDirectory() as state:
        own = values(run(state, CALDAV_PASSWORD="hunter22", CALDAV_USER="sam"))
        assert own["password"] == "hunter22" and own["user"] == "sam"
        assert not os.path.exists(os.path.join(state, "secrets", "caldav_password"))
        elsewhere = values(
            run(state, CALDAV_PASSWORD="vik-pw", CALDAV_USER="dana", CALDAV_DEFAULT_URL="http://nas:8090/dav/")
        )
        assert elsewhere["url"] == "http://nas:8090/dav/"
        assert elsewhere["default_user"] == "dana" and elsewhere["default_password"] == "vik-pw"


def test_off_writes_nothing_and_starts_nothing():
    with tempfile.TemporaryDirectory() as state:
        proc = run(state, OMI_BUNDLED_CALDAV="0", tail=SHOW + '; omi_caldav_start; printf "\\0%s" "${RADICALE_PID:-}"')
        got = values(proc)
        assert got["url"] == "" and got["password"] == ""
        assert proc.stdout.split("\0")[6] == ""
        assert not os.path.exists(os.path.join(state, "radicale"))
        assert not os.path.exists(os.path.join(state, "secrets"))


def test_old_calendars_are_copied_once_and_never_over_existing_ones():
    with tempfile.TemporaryDirectory() as state:
        legacy = os.path.join(state, "legacy")
        os.makedirs(os.path.join(legacy, "collections", "collection-root", "omi", "tasks"))
        open(os.path.join(legacy, "collections", "collection-root", "omi", "tasks", "a.ics"), "w").write("OLD")
        run(state, OMI_RADICALE_LEGACY=legacy)
        copied = os.path.join(state, "radicale", "collections", "collection-root", "omi", "tasks", "a.ics")
        assert open(copied).read() == "OLD"
        open(copied, "w").write("NEW")
        run(state, OMI_RADICALE_LEGACY=legacy)
        assert open(copied).read() == "NEW", "a later start must not overwrite the live calendars"


def _radicale_bin():
    for candidate in (os.environ.get("OMI_RADICALE_BIN"), "/opt/radicale/bin/radicale"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def _propfind(user, password):
    request = urllib.request.Request(f"http://127.0.0.1:5232/{user}/", method="PROPFIND", headers={"Depth": "0"})
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_live_the_server_starts_and_checks_the_password():
    binary = _radicale_bin()
    if binary is None:
        print("  (skipped: no radicale binary; set OMI_RADICALE_BIN)")
        return
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 5232)) == 0:
            print("  (skipped: something already listens on 5232)")
            return
    with tempfile.TemporaryDirectory() as state:
        os.makedirs(os.path.join(state, "logs"))
        proc = run(
            state,
            OMI_RADICALE_BIN=binary,
            tail=SHOW + '; omi_caldav_start; printf "\\0%s" "$RADICALE_PID"',
        )
        got = values(proc)
        pid = int(proc.stdout.split("\0")[6])
        try:
            for _ in range(50):
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", 5232)) == 0:
                        break
                time.sleep(0.2)
            assert _propfind("omi", got["password"]) == 207
            assert _propfind("omi", "wrong") == 401
        finally:
            os.kill(pid, 15)
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
