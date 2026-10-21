"""Chat model shortcuts ("/adult" → merge-proxy model id) for self-hosted deployments.

One Redis hash for the whole deployment, like the dashboard's other omi:model:*
keys: the dashboard card and the app's /model sheet both write it.
"""

from __future__ import annotations

from typing import Optional

import database.redis_db as redis_db

CHAT_MODEL_SHORTCUTS_KEY = 'omi:model:chat_shortcuts'


def _text(value: object) -> str:
    return value.decode('utf-8') if isinstance(value, bytes) else str(value)


@redis_db.try_catch_decorator
def get_chat_model_shortcuts() -> Optional[dict[str, str]]:
    raw = redis_db.r.hgetall(CHAT_MODEL_SHORTCUTS_KEY) or {}
    return {_text(k): _text(v) for k, v in raw.items()}


@redis_db.try_catch_decorator
def set_chat_model_shortcut(name: str, model: str) -> None:
    redis_db.r.hset(CHAT_MODEL_SHORTCUTS_KEY, name, model)


@redis_db.try_catch_decorator
def delete_chat_model_shortcut(name: str) -> None:
    redis_db.r.hdel(CHAT_MODEL_SHORTCUTS_KEY, name)
