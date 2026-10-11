"""Chat model shortcuts for self-hosted deployments ("/adult", "/code", ...).

A shortcut is a name the user types at the start of an Ask Omi message to have
that one reply written by a different (usually bigger) model than the base
chat model. The name → merge-proxy model id map lives in Redis
(``omi:model:chat_shortcuts``), seeded by ``OMI_CHAT_MODEL_SHORTCUTS``.

Sticky use ("/adult toggle") is client state: the app sends the shortcut name
on every message while the toggle is on. The server only ever resolves one
turn at a time.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import database.chat_model_shortcuts as shortcuts_db
from utils.llm.model_config import is_local_mode

logger = logging.getLogger(__name__)

# The shortcut every self-hosted deployment offers, configured or not, so the
# app can prompt "pick a model for /adult" instead of hiding the command.
DEFAULT_SHORTCUT_NAMES = ('adult',)

# Names the mobile app uses for its own slash commands; a shortcut must not
# shadow them or "/model" would stop opening the model sheet.
RESERVED_SHORTCUT_NAMES = frozenset({'help', 'model', 'models', 'clear', 'apps', 'review', 'toggle', 'on', 'off'})

_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]{0,23}$')
_LEADING_SHORTCUT_RE = re.compile(r'^\s*/([A-Za-z][A-Za-z0-9_-]{0,23})(?:\s+(.*))?$', re.DOTALL)

SHORTCUTS_ENV = 'OMI_CHAT_MODEL_SHORTCUTS'


class InvalidShortcutName(ValueError):
    pass


def normalize_shortcut_name(name: str) -> str:
    """Lower-case, strip a leading '/', and validate. Raises InvalidShortcutName."""
    normalized = (name or '').strip().lstrip('/').lower()
    if not _NAME_RE.match(normalized):
        raise InvalidShortcutName('Shortcut names are 1-24 chars: a letter, then letters, digits, "-" or "_".')
    if normalized in RESERVED_SHORTCUT_NAMES:
        raise InvalidShortcutName(f'"/{normalized}" is a built-in chat command.')
    return normalized


def _env_shortcuts() -> dict[str, str]:
    """``OMI_CHAT_MODEL_SHORTCUTS`` as JSON ({"adult": "llama/big"}) or "adult=llama/big,code=llama/coder"."""
    raw = os.getenv(SHORTCUTS_ENV, '').strip()
    if not raw:
        return {}
    pairs: dict[str, str] = {}
    try:
        parsed = json.loads(raw)
        items = parsed.items() if isinstance(parsed, dict) else []
    except json.JSONDecodeError:
        items = [part.split('=', 1) for part in raw.split(',') if '=' in part]
    for name, model in items:
        try:
            normalized = normalize_shortcut_name(str(name))
        except InvalidShortcutName:
            logger.warning('Ignoring invalid %s entry %r', SHORTCUTS_ENV, name)
            continue
        if str(model).strip():
            pairs[normalized] = str(model).strip()
    return pairs


def get_shortcuts() -> dict[str, str]:
    """Configured shortcuts: env seed overlaid by Redis (dashboard/app edits win)."""
    shortcuts = _env_shortcuts()
    shortcuts.update({k: v for k, v in (shortcuts_db.get_chat_model_shortcuts() or {}).items() if v})
    return shortcuts


def list_shortcut_names() -> list[str]:
    """Every name the app should offer: the defaults plus anything configured."""
    names = set(DEFAULT_SHORTCUT_NAMES) | set(get_shortcuts())
    return sorted(names)


def resolve_shortcut_model(name: str) -> Optional[str]:
    try:
        normalized = normalize_shortcut_name(name)
    except InvalidShortcutName:
        return None
    return get_shortcuts().get(normalized)


def set_shortcut(name: str, model: str) -> str:
    normalized = normalize_shortcut_name(name)
    model = (model or '').strip()
    if not model:
        raise ValueError('model is required')
    shortcuts_db.set_chat_model_shortcut(normalized, model)
    return normalized


def delete_shortcut(name: str) -> str:
    normalized = normalize_shortcut_name(name)
    shortcuts_db.delete_chat_model_shortcut(normalized)
    return normalized


@dataclass(frozen=True)
class LeadingShortcut:
    name: str
    text: str


def parse_leading_shortcut(text: str, known_names: list[str] | set[str]) -> Optional[LeadingShortcut]:
    """Split "/adult what did I say about X?" into ("adult", "what did I say about X?").

    Only names in ``known_names`` match, so a message that merely starts with a
    path ("/etc/hosts is broken") is left alone. A bare "/adult" with no message
    is not a shortcut turn — there is nothing to answer.
    """
    match = _LEADING_SHORTCUT_RE.match(text or '')
    if not match:
        return None
    name = match.group(1).lower()
    rest = (match.group(2) or '').strip()
    if name not in set(known_names) or not rest:
        return None
    return LeadingShortcut(name=name, text=rest)


@dataclass(frozen=True)
class TurnShortcut:
    """How one chat turn should be routed.

    ``name`` set with ``model`` None means the user asked for a shortcut that has
    no model configured — the caller should say so rather than silently answer
    with the base model.
    """

    name: Optional[str]
    model: Optional[str]
    text: str


def resolve_turn_shortcut(text: str, requested: Optional[str]) -> TurnShortcut:
    """Resolve a turn's shortcut from an explicit request field or a leading "/name".

    The explicit field (what the app sends while "/adult toggle" is on) wins; a
    redundant "/name " prefix on the same text is stripped either way.
    """
    if requested and requested.strip():
        try:
            name = normalize_shortcut_name(requested)
        except InvalidShortcutName:
            return TurnShortcut(name=requested.strip().lstrip('/').lower(), model=None, text=text)
        parsed = parse_leading_shortcut(text, [name])
        return TurnShortcut(name=name, model=resolve_shortcut_model(name), text=parsed.text if parsed else text)

    parsed = parse_leading_shortcut(text, list_shortcut_names())
    if parsed is None:
        return TurnShortcut(name=None, model=None, text=text)
    return TurnShortcut(name=parsed.name, model=resolve_shortcut_model(parsed.name), text=parsed.text)


def unconfigured_shortcut_reply(name: str) -> str:
    return (
        f"The /{name} model isn't set up yet, so I didn't answer with it. "
        "Type /model here (or open the server dashboard → Chat model shortcuts) to pick one, then send again."
    )


@dataclass(frozen=True)
class ChatTurnRouting:
    """The answer model and messages for one chat turn (see route_chat_turn)."""

    model: Optional[str]
    messages: list
    unconfigured_reply: Optional[str] = None


def route_chat_turn(messages: list, model_shortcut: Optional[str]) -> ChatTurnRouting:
    """Pick this turn's answer model from ``model_shortcut`` or a leading "/name" on the newest message.

    Blocking (reads Redis) — call through run_blocking. Outside local-LLM mode
    nothing changes. A typed "/adult " prefix is stripped from the text the
    model sees; an unconfigured shortcut yields ``unconfigured_reply`` so the
    caller can answer with that instead of silently using the base model.
    """
    routed = list(messages)
    if not routed:
        return ChatTurnRouting(None, routed)
    if not is_local_mode():
        if model_shortcut:
            logger.info('ignoring model_shortcut outside local-LLM mode')
        return ChatTurnRouting(None, routed)
    last = routed[-1]
    text = getattr(last, 'text', '') or '' if getattr(last, 'sender', None) == 'human' else ''
    turn = resolve_turn_shortcut(text, model_shortcut)
    if turn.name and not turn.model:
        return ChatTurnRouting(None, routed, unconfigured_shortcut_reply(turn.name))
    if not turn.model:
        return ChatTurnRouting(None, routed)
    logger.info('chat turn uses model shortcut /%s', turn.name)
    if text and turn.text != text:
        routed[-1] = last.model_copy(update={'text': turn.text})
    return ChatTurnRouting(turn.model, routed)
