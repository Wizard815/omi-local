"""Server-side v2 flag authority shared by admission and exclusive mentor dispatch.

omi-local: the flag is read from the Omi flag store only (utils/omi_flags.py,
this server's own Firestore). The PostHog source and the dual-read compare are
gone, so OMI_FLAGS_SOURCE is retired; an absent or unreadable flag denies.
"""

import logging
import os
import time
from threading import Lock

from config.proactivity_v2 import ProactivityDenied
from utils import omi_flags

logger = logging.getLogger(__name__)

ERROR_TTL_SECONDS = 60
FLAG_KEY = 'proactivity_v2'


class ProactivityFlagUnavailable(ProactivityDenied):
    """Content-free failure; never retains a store exception or response."""

    def __init__(self, error_type: str, http_status: int | None = None):
        super().__init__('flag_unavailable')
        self.error_type = error_type
        self.http_status = http_status


_diagnostic_lock = Lock()
_next_warning_at = 0.0


def _log_flag_error(error_type: str) -> None:
    global _next_warning_at
    with _diagnostic_lock:
        now = time.monotonic()
        if now < _next_warning_at:
            return
        _next_warning_at = now + ERROR_TTL_SECONDS
    logger.warning('proactivity_v2_flag_unavailable error_type=%s', error_type)


def enabled(uid: str) -> bool:
    try:
        resolved = omi_flags.resolve(FLAG_KEY, uid)
    except Exception as error:
        error_type = ''.join(c for c in type(error).__name__ if c.isascii() and (c.isalnum() or c == '_'))[:64]
        _log_flag_error(error_type or 'unknown')
        raise ProactivityFlagUnavailable(error_type or 'unknown') from None
    if resolved is None:
        # Absent or unreadable store: fail closed.
        _log_flag_error('omi_flag_absent')
        raise ProactivityFlagUnavailable('omi_flag_absent')
    return resolved.enabled


def mentor_pipeline(uid: str) -> str | None:
    """Resolve the exclusive mentor lane for this user: 'v2' or no lane.

    The legacy mentor pipeline was deleted after v2 reached 100%: every
    non-'v2' answer (unflagged user, non-cohort host, mid-flight invalid flip)
    means no mentor dispatch, never the removed path. Flag errors deny the
    same way ``enabled`` does — ``ProactivityFlagUnavailable`` with the
    rate-limited diagnostic — instead of falling back to a deleted lane.
    """
    if os.getenv('MENTOR_PIPELINE') != 'cohort':
        # cohort is the only supported mode; unset, a stale legacy/v2 value, or
        # a typo is a config error that resolves no lane (and the runtime env
        # validator rejects it at deploy time).
        return None
    return 'v2' if enabled(uid) else None
