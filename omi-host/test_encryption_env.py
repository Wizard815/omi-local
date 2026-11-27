#!/usr/bin/env python3
"""encryption-env.sh: where ENCRYPTION_SECRET comes from.

entrypoint.sh sources encryption-env.sh under `set -uo pipefail`; this runs it
the same way, with compose's empty-string ENCRYPTION_SECRET, against a scratch
data volume laid out as entrypoint.sh leaves it (empty logs/, redis/ and
firebase-export/).

What this pins down:

  * a new install gets its own random key, owner-only, and the same key comes
    back on every later start (a changed key makes stored data unreadable),
  * a volume that was used before this change keeps the old published default,
    with a warning, and no key file is written: its data was encrypted under it,
  * an operator's own ENCRYPTION_SECRET wins and nothing is written,
  * a saved key wins over the "used volume" rule.

Run:  python3 test_encryption_env.py     (no pytest required)
"""

import os
import stat
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "encryption-env.sh")
PUBLISHED = "omi_local_dev_harness_32_byte_test_secret_not_prod"


def new_volume(state):
    for name in ("logs", "redis", "firebase-export"):
        os.mkdir(os.path.join(state, name))


def run(state, secret=""):
    proc = subprocess.run(
        ["bash", "-c", 'set -uo pipefail; . "$1"; printf "\\0%s" "$ENCRYPTION_SECRET"', "_", SCRIPT],
        env={"PATH": os.environ["PATH"], "STATE": state, "ENCRYPTION_SECRET": secret},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.split("\0")[1], proc.stderr


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_new_install_gets_its_own_key_and_keeps_it():
    with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as other:
        new_volume(state)
        new_volume(other)
        first, _ = run(state)
        key_file = os.path.join(state, "secrets", "encryption_secret")
        assert first != PUBLISHED
        assert len(first.encode()) >= 32, "utils/encryption.py refuses a key under 32 bytes"
        assert mode(key_file) == 0o600, oct(mode(key_file))
        assert mode(os.path.join(state, "secrets")) == 0o700
        assert not os.path.exists(key_file + ".tmp")
        # Later starts: redis and Firestore have written to the volume by now.
        open(os.path.join(state, "redis", "appendonly.aof"), "w").close()
        open(os.path.join(state, "firebase-export", "firebase-export-metadata.json"), "w").close()
        assert run(state)[0] == first, "a restart must reuse the key, or stored data cannot be read"
        assert run(other)[0] != first, "two installs must not share a key"


def test_a_used_volume_keeps_the_published_default_and_warns():
    for used in ("firebase-export/firebase-export-metadata.json", "redis/appendonly.aof", "secrets/local_auth_jwt_secret"):
        with tempfile.TemporaryDirectory() as state:
            new_volume(state)
            path = os.path.join(state, used)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
            for _ in range(2):  # and it stays that way on the next start
                secret, err = run(state)
                assert secret == PUBLISHED, f"{used}: data written under the old default must stay readable"
                assert "WARNING" in err, err
            assert not os.path.exists(os.path.join(state, "secrets", "encryption_secret"))


def test_an_operator_key_wins_and_nothing_is_written():
    with tempfile.TemporaryDirectory() as state:
        new_volume(state)
        secret, err = run(state, secret="operator-pinned-key-of-at-least-32-bytes")
        assert secret == "operator-pinned-key-of-at-least-32-bytes"
        assert "WARNING" not in err
        assert not os.path.exists(os.path.join(state, "secrets"))
        _, err = run(state, secret=PUBLISHED)
        assert "WARNING" in err, "pinning the published value should still be called out"


def test_a_saved_key_wins_on_a_used_volume():
    with tempfile.TemporaryDirectory() as state:
        new_volume(state)
        os.mkdir(os.path.join(state, "secrets"))
        open(os.path.join(state, "firebase-export", "firebase-export-metadata.json"), "w").close()
        with open(os.path.join(state, "secrets", "encryption_secret"), "w") as f:
            f.write("a-key-saved-on-the-volume-of-32-bytes-or-more\n")
        secret, err = run(state)
        assert secret == "a-key-saved-on-the-volume-of-32-bytes-or-more"
        assert "WARNING" not in err


def main() -> None:
    tests = [
        test_a_new_install_gets_its_own_key_and_keeps_it,
        test_a_used_volume_keeps_the_published_default_and_warns,
        test_an_operator_key_wins_and_nothing_is_written,
        test_a_saved_key_wins_on_a_used_volume,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
