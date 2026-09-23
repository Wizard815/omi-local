"""Shared helper for backend/scripts/*.py run via a plain `docker exec`.

entrypoint.sh `export`s a long, growing list of vars (Firestore/Auth emulator
host, ENCRYPTION_SECRET, ADMIN_KEY, REDIS_DB_*, ...) for the backend process
it launches — but entrypoint.sh itself stays PID 1 in the container (it
never `exec`s into uvicorn, see the shutdown/signal-forwarding comment
there), so a fresh `docker exec` session doesn't inherit any of them; it's a
sibling process tree, not a child of entrypoint.sh's shell.

Every earlier fix for this was a hardcoded os.environ.setdefault(...) block
duplicating a handful of entrypoint.sh's exports per script — correct at the
time, but it silently drifts every time entrypoint.sh's own list grows (see
the ENCRYPTION_SECRET ModuleNotFoundError-turned-ValueError this fixes).
Since entrypoint.sh really is PID 1, its exported environment is readable
from any docker exec session in the same container via /proc/1/environ —
one source of truth instead of N copies to keep in sync by hand.
"""

from pathlib import Path


def inherit_pid1_env() -> None:
    """setdefault() every KEY=VALUE entrypoint.sh (PID 1) has exported.

    setdefault, not overwrite: an explicit `docker exec -e FOO=bar` or a
    value already set earlier in this same script wins over PID 1's copy.
    Silently does nothing if /proc/1/environ isn't readable (e.g. run
    outside a container, or as a non-root user without access) — those
    callers are expected to already have what they need some other way.
    """
    import os

    try:
        raw = Path('/proc/1/environ').read_bytes()
    except OSError:
        return

    for entry in raw.split(b'\x00'):
        if not entry or b'=' not in entry:
            continue
        key, _, value = entry.partition(b'=')
        try:
            os.environ.setdefault(key.decode(), value.decode())
        except UnicodeDecodeError:
            continue
