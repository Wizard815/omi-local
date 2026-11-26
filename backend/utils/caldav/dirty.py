"""Change-triggered CalDAV sync marks, kept apart from the sync engine.

The action-item store marks a user dirty on every write. Importing ``utils.caldav.sync`` for that
pulls in the whole sync engine (CalDAV client, iCalendar, every store it reads), so the mark lives
here with only Redis behind it. ``utils.caldav.sync`` re-exports these names.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator, Optional, Set

import database.redis_db as redis_db

DIRTY_KEY = 'caldav:dirty'

# Inside ``one_mark``: the users marked dirty, held until the batch ends (one mark, not one per item).
_HELD_MARKS: contextvars.ContextVar[Optional[Set[str]]] = contextvars.ContextVar('caldav_held_marks', default=None)


def mark_dirty(uid: str) -> None:
    """A task or day summary changed: sync this user at the next short pass (seconds, not the
    hour). Called from the action-item store and the day bundle; never raises."""
    held = _HELD_MARKS.get()
    if held is not None:
        held.add(uid)
        return
    try:
        redis_db.r.sadd(DIRTY_KEY, uid)
    except Exception:  # noqa: BLE001 — the hourly full sync still covers it
        pass


@contextmanager
def one_mark() -> Iterator[None]:
    """A batch of changes (Approve all) marks each user dirty once, when it ends, instead of once
    per item. Work offloaded with run_blocking carries the context, so its marks are held too."""
    held: Set[str] = set()
    token = _HELD_MARKS.set(held)
    try:
        yield
    finally:
        _HELD_MARKS.reset(token)
        for uid in sorted(held):
            mark_dirty(uid)
