"""Whose CalDAV objects are whose, when every Omi user on a server shares one CalDAV login
(omi-host's bundled Radicale): the owner tag in every UID Omi writes, the Scope a sync writes
and reads with, the names of a user's own collections, and the registry of which Omi user
syncs into which collection (a Redis hash, so the app's picker can say "Used by Bob").
utils/caldav/sync.py explains the rules; this module only holds them."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import database.redis_db as redis_db
from database.auth import get_user_from_uid, get_user_full_name
from utils.caldav import ical

DEFAULT_CALENDAR_NAME = 'Omi'
DEFAULT_TASKS_NAME = 'Omi Tasks'


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _hget(key: str, field_name: str) -> str:
    try:
        value = redis_db.r.hget(key, field_name)
    except Exception:  # noqa: BLE001
        return ''
    return '' if value is None else _text(value)


def _hset(key: str, field_name: str, value: str) -> None:
    try:
        redis_db.r.hset(key, field_name, value)
    except Exception:  # noqa: BLE001 — the registry is advisory: the picker just shows less
        pass


def _hdel(key: str, field_name: str) -> None:
    try:
        redis_db.r.hdel(key, field_name)
    except Exception:  # noqa: BLE001
        pass


def _hgetall(key: str) -> Dict[str, str]:
    try:
        raw = redis_db.r.hgetall(key) or {}
    except Exception:  # noqa: BLE001
        return {}
    return {_text(k): _text(v) for k, v in raw.items()}


# ---- whose objects these are --------------------------------------------------------------


def owner_tag(uid: str) -> str:
    """The owner part of every UID Omi writes: 'u' + 8 hex. The 'u' keeps it apart from the
    old unscoped UIDs, whose ids start with a digit (dates) or a hex digit (conversation ids)."""
    return 'u' + hashlib.sha256(uid.encode('utf-8')).hexdigest()[:8]


_SCOPED = re.compile(r'^(u[0-9a-f]{8})-(.+)$')


def parse_owned(prefix: str, uid_value: str, tag: str) -> Tuple[str, Optional[str]]:
    """('mine', rest) for this owner's UID or an old unscoped one, ('foreign', None) for another
    Omi user's, ('none', None) for one Omi did not write with this prefix."""
    if not uid_value.startswith(prefix):
        return 'none', None
    rest = uid_value[len(prefix) :].split('@', 1)[0]
    match = _SCOPED.match(rest)
    if match is None:
        return 'mine', rest  # unscoped (written before owners): ids are unique, the store decides
    if match.group(1) != tag:
        return 'foreign', None
    return 'mine', match.group(2)


def owner_label(uid: str) -> str:
    """How the user is called in collection names: their name, else the local part of their
    email (a self-hosted login), else a short id."""
    try:
        name = (get_user_full_name(uid) or '').strip()
        if name:
            return name
        user = get_user_from_uid(uid) or {}
        email = str(user.get('email') or '')
        if email:
            return email.split('@', 1)[0]
    except Exception:  # noqa: BLE001 — a name is cosmetic; the id still tells users apart
        pass
    return uid[:8]


def personal_names(label: str) -> Tuple[str, str]:
    """(calendar, task list) names a user gets when they have not picked any."""
    return f'{DEFAULT_CALENDAR_NAME} – {label}', f'{DEFAULT_TASKS_NAME} – {label}'


def parent_summary(label: str) -> str:
    return f"{label}'s tasks"


@dataclass
class Scope:
    """Whose objects a sync writes and reads: the owner tag, whether this user still uses the
    old unscoped task/event UIDs (migrated, same collections), and the parent task in parent mode."""

    tag: str
    legacy: bool = False
    parent_uid: str = ''
    parent_summary: str = ''
    shared_tasks: bool = False  # another Omi user syncs into the same task list

    def task_uid(self, task_id: str) -> str:
        if self.legacy:
            return f'{ical.TASK_UID_PREFIX}{task_id}{ical.UID_DOMAIN}'
        return f'{ical.TASK_UID_PREFIX}{self.tag}-{task_id}{ical.UID_DOMAIN}'

    def event_uid(self, prefix: str, key: str) -> str:
        if self.legacy:
            return f'{prefix}{key}{ical.UID_DOMAIN}'
        return f'{prefix}{self.tag}-{key}{ical.UID_DOMAIN}'

    def day_uid(self, date_str: str) -> str:
        # Day UIDs collided between users (one per date), so they are scoped for everyone.
        return f'{ical.DAY_UID_PREFIX}{self.tag}-{date_str}{ical.UID_DOMAIN}'


def scope_for(uid: str) -> Scope:
    return Scope(tag=owner_tag(uid))


# ---- who uses which collection (one CalDAV account, several Omi users) ---------------------

REGISTRY_KEY = 'caldav:collection-users'


def collection_users(href: str) -> Dict[str, str]:
    """{omi uid: label} of the users whose sync is bound to this collection (Redis memo)."""
    raw = _hget(REGISTRY_KEY, href)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def register_collections(uid: str, label: str, hrefs: List[str]) -> None:
    """Remember that ``uid`` syncs into ``hrefs`` (and no longer into any other collection)."""
    wanted = {h for h in hrefs if h}
    for href, raw in _hgetall(REGISTRY_KEY).items():
        try:
            users = json.loads(raw)
        except ValueError:
            users = {}
        if not isinstance(users, dict):
            users = {}
        if href in wanted:
            continue
        if uid in users:
            users.pop(uid, None)
            if users:
                _hset(REGISTRY_KEY, href, json.dumps(users, sort_keys=True))
            else:
                _hdel(REGISTRY_KEY, href)
    for href in wanted:
        users = collection_users(href)
        if users.get(uid) != label:
            users[uid] = label
            _hset(REGISTRY_KEY, href, json.dumps(users, sort_keys=True))


def others_on(uid: str, href: str) -> Dict[str, str]:
    return {k: v for k, v in collection_users(href).items() if k != uid}
