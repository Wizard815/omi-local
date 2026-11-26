"""Where the Home Assistant code's blocking reads and writes run (omi-local).

On the event loop (the chat agent, the HTTP routes, the scheduler) a Firestore call goes to
``db_executor`` through ``run_blocking``. An MCP tool handler already runs on a
``db_executor`` worker: ``run_inline`` drives the coroutine on a private loop in that thread
and lets ``db`` call Firestore right there, so the worker never waits on a second slot of
its own pool (backend/AGENTS.md, deadlock rule 1).
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable

from utils.executors import db_executor, run_blocking
from utils.outbound_http import run_sync

_INLINE = contextvars.ContextVar('home_assistant_inline_db', default=False)


async def db(fn: Callable[..., Any], *args: Any) -> Any:
    if _INLINE.get():
        return fn(*args)
    return await run_blocking(db_executor, fn, *args)


def run_inline(coro: Any) -> Any:
    """Run a Home Assistant coroutine to completion from a worker thread (never the loop)."""
    token = _INLINE.set(True)
    try:
        return run_sync(coro)
    finally:
        _INLINE.reset(token)
