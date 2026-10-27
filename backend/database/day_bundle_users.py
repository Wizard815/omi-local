"""Users with conversations the automatic day bundle may have to fold.

A self-hosted stack has no user directory to scan every hour, so the hold
path registers the owner here (a Redis set) whenever it stores a clip; the
bundle scheduler walks this set. Membership expires after
INACTIVE_TTL_SECONDS without a new clip, so a retired account stops costing a
pending-days read every hour.
"""

from __future__ import annotations

import logging
from typing import List

import database.redis_db as redis_db

logger = logging.getLogger(__name__)

KEY = 'day_bundle:active_users'
INACTIVE_TTL_SECONDS = 30 * 24 * 3600


def mark_active(uid: str) -> None:
    try:
        redis_db.r.sadd(KEY, uid)
        redis_db.r.expire(KEY, INACTIVE_TTL_SECONDS)
    except Exception as error:  # noqa: BLE001 — registration is best effort
        logger.warning('day_bundle: could not register uid=%s: %s', uid, type(error).__name__)


def active_users() -> List[str]:
    try:
        return sorted(m.decode() if isinstance(m, bytes) else str(m) for m in redis_db.r.smembers(KEY))
    except Exception as error:  # noqa: BLE001
        logger.warning('day_bundle: could not list active users: %s', type(error).__name__)
        return []
