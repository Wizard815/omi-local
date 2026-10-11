"""Hourly in-process job runner for self-hosted deployments.

Upstream runs the notifications/daily-summary job (backend/modal/job.py →
utils.other.notifications.start_cron_job) as a Cloud Run Job that Cloud
Scheduler fires at the top of every UTC hour. A self-hosted omi-local
container only runs uvicorn, so nothing ever fired it: no automatic daily
recap, ever — only the on-demand "generate recap" button worked.

With OMI_LOCAL_SCHEDULER=1 (omi-host's entrypoint sets it) the backend fires
the same job itself shortly after every hour. The job already serializes
overlapping runs with a Redis lock and per-user/day locks, so a second backend
replica running this too is harmless.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

SCHEDULER_ENV = 'OMI_LOCAL_SCHEDULER'
# A couple of minutes past the hour, so a backend that restarts right on the
# hour still catches that hour's tick.
TICK_MINUTE = 2


def local_scheduler_enabled() -> bool:
    return os.getenv(SCHEDULER_ENV, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def seconds_until_next_tick(now: datetime, minute: int = TICK_MINUTE) -> float:
    """Seconds from ``now`` until the next HH:``minute``:00 (strictly in the future)."""
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(hours=1)
    return (candidate - now).total_seconds()


async def run_hourly(
    job: Callable[[], Awaitable[None]],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_ticks: Optional[int] = None,
) -> None:
    """Run ``job`` once per hour forever (or ``max_ticks`` times). A failing tick never stops the loop."""
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        await sleep(seconds_until_next_tick(now()))
        ticks += 1
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('local_scheduler tick failed')


async def run_local_scheduler() -> None:
    from utils.other.notifications import start_cron_job

    logger.info('local_scheduler started: daily-summary/notification job every hour at :%02d', TICK_MINUTE)
    await run_hourly(start_cron_job)
