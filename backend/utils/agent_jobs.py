"""Agent jobs (omi-local): placeholder until the agent-job targets land."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


async def follow(item: Dict[str, Any], now: datetime) -> str:
    return 'skipped'
