"""Shared helper for backend/scripts/*.py run via a plain `docker exec`.

entrypoint.sh exports a long, growing list of vars (Firestore/Auth emulator
host, ENCRYPTION_SECRET, ADMIN_KEY, REDIS_DB_*, ...) for the backend process
it launches — but entrypoint.sh itself stays PID 1 in the container (it
never `exec`s into uvicorn, see the shutdown/signal-forwarding comment
there), so a fresh `docker exec` session doesn't inherit any of them; it's a
sibling process tree, not a child of entrypoint.sh's shell.

/proc/1/environ is NOT the fix, despite looking like an obvious one:
/proc/<pid>/environ is a snapshot of the environment at that process's
*creation* (execve), not a live view of its shell's current variable table.
entrypoint.sh's `export FOO=bar` statements happen well after PID 1 itself
was created, so they update entrypoint.sh's in-process table (and anything
it forks from that point on) but never retroactively appear in PID 1's own
/proc/1/environ. The uvicorn backend process, by contrast, was started via
`uvicorn main:app & BACKEND_PID=$!` *after* every export, so its own
/proc/<pid>/environ snapshot genuinely has the full, correct set. Read that
process's environment instead of PID 1's.
"""

import re
from pathlib import Path
from typing import Optional

_UVICORN_CMDLINE_RE = re.compile(rb'uvicorn')


def _find_backend_pid() -> Optional[int]:
    """Find the running `uvicorn main:app` process by scanning /proc."""
    proc = Path('/proc')
    if not proc.is_dir():
        return None
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / 'cmdline').read_bytes()
        except OSError:
            continue
        if _UVICORN_CMDLINE_RE.search(cmdline):
            return int(entry.name)
    return None


def inherit_pid1_env() -> None:
    """setdefault() every KEY=VALUE the running backend process was started with.

    Despite the name (kept for compatibility with existing call sites),
    reads the uvicorn backend process's environment, not PID 1's — see the
    module docstring for why PID 1 doesn't actually have what we need.
    Falls back to PID 1 if no uvicorn process is found (e.g. the backend
    isn't up yet), which at least won't make things worse. setdefault, not
    overwrite: an explicit `docker exec -e FOO=bar` or a value already set
    earlier in this same script wins. Silently does nothing if neither
    process's /proc/<pid>/environ is readable.
    """
    import os

    pid = _find_backend_pid() or 1
    try:
        raw = Path(f'/proc/{pid}/environ').read_bytes()
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
