"""Users with conversations the automatic day bundle may have to fold.

A self-hosted stack has no user directory to scan every hour, so the hold
path registers the owner here (a Redis hash of uid → last-clip time) whenever
it stores a clip; the bundle scheduler walks it. A member whose last clip is
older than INACTIVE_TTL_SECONDS is dropped on read, so a retired account stops
costing a pending-days read every hour while the rest of the stack stays busy.
"""

from __future__ import annotations

import logging
import time
from typing import List

import database.redis_db as redis_db

logger = logging.getLogger(__name__)

KEY = 'day_bundle:active_users_at'  # hash: uid -> unix time of the last registration
LEGACY_KEY = 'day_bundle:active_users'  # the pre-fix set (one shared TTL); migrated on first read
INACTIVE_TTL_SECONDS = 30 * 24 * 3600


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def mark_active(uid: str) -> None:
    try:
        redis_db.r.hset(KEY, uid, int(time.time()))
        # Key-level GC only: when the whole stack goes quiet this long, the hash itself expires.
        redis_db.r.expire(KEY, INACTIVE_TTL_SECONDS)
    except Exception as error:  # noqa: BLE001 — registration is best effort
        logger.warning('day_bundle: could not register uid=%s: %s', uid, type(error).__name__)


def _migrate_legacy() -> None:
    members = redis_db.r.smembers(LEGACY_KEY)
    if not members:
        return
    now = int(time.time())
    for member in members:
        member = _text(member)
        if not redis_db.r.hexists(KEY, member):
            redis_db.r.hset(KEY, member, now)
    redis_db.r.delete(LEGACY_KEY)


def active_users() -> List[str]:
    try:
        _migrate_legacy()
        cutoff = time.time() - INACTIVE_TTL_SECONDS
        active: List[str] = []
        stale: List[str] = []
        for member, last in (redis_db.r.hgetall(KEY) or {}).items():
            member = _text(member)
            try:
                fresh = float(_text(last)) >= cutoff
            except (TypeError, ValueError):
                fresh = False
            (active if fresh else stale).append(member)
        if stale:
            redis_db.r.hdel(KEY, *stale)
        return sorted(active)
    except Exception as error:  # noqa: BLE001
        logger.warning('day_bundle: could not list active users: %s', type(error).__name__)
        return []
