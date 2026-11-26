#!/usr/bin/env python3
"""auth-env.sh: the key that signs login sessions.

entrypoint.sh sources auth-env.sh under `set -uo pipefail`; this runs it the
same way, with compose's empty-string LOCAL_AUTH_JWT_SECRET, against a scratch
data volume.

What this pins down:

  * with no key set, one is generated on first start, owner-only, and the same
    key comes back on every later start (sessions survive restarts),
  * the secrets directory is created owner-only, but an existing directory's
    mode is left alone,
  * an operator's own LOCAL_AUTH_JWT_SECRET wins and nothing is written,
  * the memory list's cursor key (MEMORY_V3_CURSOR_SECRET) is generated and kept the
    same way: without it /v3/memories hands out no cursor and every read costs two of
    the process-wide read admissions.

Run:  python3 test_auth_env.py     (no pytest required)
"""

import os
import stat
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "auth-env.sh")


def run_both(state, secret="", cursor_secret=""):
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'set -uo pipefail; . "$1"; printf "\\0%s\\0%s" "$LOCAL_AUTH_JWT_SECRET" "$MEMORY_V3_CURSOR_SECRET"',
            "_",
            SCRIPT,
        ],
        env={
            "PATH": os.environ["PATH"],
            "STATE": state,
            "LOCAL_AUTH_JWT_SECRET": secret,
            "MEMORY_V3_CURSOR_SECRET": cursor_secret,
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    _, login, cursor = proc.stdout.split("\0")
    return login, cursor


def run(state, secret=""):
    return run_both(state, secret)[0]


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_key_is_generated_once_and_kept():
    with tempfile.TemporaryDirectory() as state:
        first = run(state)
        key_file = os.path.join(state, "secrets", "local_auth_jwt_secret")
        assert len(first) >= 48, first
        assert mode(key_file) == 0o600, oct(mode(key_file))
        assert mode(os.path.join(state, "secrets")) == 0o700
        assert run(state) == first, "a restart must reuse the key, or every device is signed out"
        assert not os.path.exists(key_file + ".tmp")


def test_an_existing_secrets_directory_keeps_its_mode():
    with tempfile.TemporaryDirectory() as state:
        os.mkdir(os.path.join(state, "secrets"), 0o750)
        os.chmod(os.path.join(state, "secrets"), 0o750)
        run(state)
        assert mode(os.path.join(state, "secrets")) == 0o750


def test_an_operator_key_wins_and_nothing_is_written():
    with tempfile.TemporaryDirectory() as state:
        login, cursor = run_both(state, secret="operator-pinned-key", cursor_secret="operator-cursor-key")
        assert (login, cursor) == ("operator-pinned-key", "operator-cursor-key")
        assert not os.path.exists(os.path.join(state, "secrets"))


def test_a_memory_cursor_key_is_generated_once_and_kept():
    with tempfile.TemporaryDirectory() as state:
        login, cursor = run_both(state)
        key_file = os.path.join(state, "secrets", "memory_cursor_secret")
        assert len(cursor) >= 48 and cursor != login, cursor
        assert mode(key_file) == 0o600, oct(mode(key_file))
        assert run_both(state)[1] == cursor, "a restart must reuse the key, or open cursors go stale"
        assert not os.path.exists(key_file + ".tmp")


def main() -> None:
    tests = [
        test_a_key_is_generated_once_and_kept,
        test_an_existing_secrets_directory_keeps_its_mode,
        test_an_operator_key_wins_and_nothing_is_written,
        test_a_memory_cursor_key_is_generated_once_and_kept,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
