"""Local usage data (omi-local): where the server's product events go.

Third-party analytics is gone. The events the backend used to send to PostHog
are kept, if at all, on this server: per-day counts and a short list of recent
events in its own Firestore (database/local_usage.py). Nothing leaves the box.

OMI_LOCAL_USAGE: on | off. Default: on for a self-hosted stack
(OMI_SELF_HOSTED=1), off otherwise.
OMI_LOCAL_USAGE_RETENTION_DAYS: days of counts kept (default 90, 1..365).
"""

import os
from typing import Mapping, Optional

from utils.self_hosting import is_self_hosted

DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 365
# Newest events kept per person; the counts carry the long tail.
RECENT_EVENTS_KEPT = 50


def usage_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if environ is None else environ
    raw = source.get('OMI_LOCAL_USAGE', '').strip().lower()
    if raw in {'1', 'on', 'true', 'yes'}:
        return True
    if raw in {'0', 'off', 'false', 'no'}:
        return False
    return is_self_hosted(source)


def retention_days(environ: Optional[Mapping[str, str]] = None) -> int:
    source = os.environ if environ is None else environ
    try:
        days = int(source.get('OMI_LOCAL_USAGE_RETENTION_DAYS', '').strip())
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return min(max(days, 1), MAX_RETENTION_DAYS)
