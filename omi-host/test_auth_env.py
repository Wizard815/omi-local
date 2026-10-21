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
  * an operator's own LOCAL_AUTH_JWT_SECRET wins and nothing is written.

Run:  python3 test_auth_env.py     (no pytest required)
"""

import os
import stat
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "auth-env.sh")


def run(state, secret=""):
    proc = subprocess.run(
        ["bash", "-c", 'set -uo pipefail; . "$1"; printf "\\0%s" "$LOCAL_AUTH_JWT_SECRET"', "_", SCRIPT],
        env={"PATH": os.environ["PATH"], "STATE": state, "LOCAL_AUTH_JWT_SECRET": secret},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.split("\0", 1)[1]


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
        assert run(state, secret="operator-pinned-key") == "operator-pinned-key"
        assert not os.path.exists(os.path.join(state, "secrets"))


def main() -> None:
    tests = [
        test_a_key_is_generated_once_and_kept,
        test_an_existing_secrets_directory_keeps_its_mode,
        test_an_operator_key_wins_and_nothing_is_written,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
