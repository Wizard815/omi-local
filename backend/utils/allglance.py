"""ALLGlance (Wizard815/ALLGlance): the family calendar/tasks gateway as a bundled integration.

The gateway is one MCP + REST + CalDAV front door over Vikunja and the GLANCE apps.
Omi reaches it three ways, and this module wires the first two from the stack's env so
a self-hosted user gets them without touching the app:

- chat: the gateway's MCP tools (allglance_get_day, allglance_list_tasks, ...) become a
  private Omi app with chat tools, exactly as POST /v1/apps/mcp would create one with a
  fixed bearer token;
- tasks: an "allglance" task integration (utils/task_integrations_ops.py) so new action
  items land in Vikunja at once, and it becomes the default when the user has none;
- CalDAV: the hourly sync (utils/caldav/sync.py) when CALDAV_DEFAULT_URL points at the
  gateway's /dav/ (tasks both ways; the server has no events calendar).

Env (omi-host/docker-compose.yml): ALLGLANCE_URL, ALLGLANCE_TOKEN. Both unset = nothing here runs.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import database.apps as apps_db
import database.redis_db as redis_db
import database.users as users_db
from utils.mcp_client import discover_mcp_tools

logger = logging.getLogger(__name__)

APP_KEY = 'allglance'
APP_NAME = 'ALLGlance'
TOOLS_CACHE_SECONDS = 600

_tools_cache: Dict[str, Any] = {'at': 0.0, 'tools': None}


def settings_from_env() -> Optional[Dict[str, str]]:
    """The gateway to use for every user, or None when the stack has none."""
    url = os.getenv('ALLGLANCE_URL', '').strip().rstrip('/')
    token = os.getenv('ALLGLANCE_TOKEN', '').strip()
    if not url:
        return None
    return {'url': url, 'mcp_url': f'{url}/mcp', 'token': token}


def app_id_for(uid: str) -> str:
    """One private app per user; the id is stable so the seed is idempotent."""
    return f'allglance-{uid}'


async def _discover_tools(settings: Dict[str, str], *, now: float) -> Optional[List[Dict[str, Any]]]:
    """The gateway's tool list, cached: every user gets the same tools from the same server."""
    if _tools_cache['tools'] is not None and now - _tools_cache['at'] < TOOLS_CACHE_SECONDS:
        return _tools_cache['tools']
    try:
        tools = await discover_mcp_tools(settings['mcp_url'], settings['token'] or None)
    except Exception as error:  # noqa: BLE001 — the gateway may be down; try again next tick
        logger.warning('allglance: tool discovery failed at %s: %s', settings['mcp_url'], error)
        return None
    # Stored the way POST /v1/apps/mcp stores them: Firestore's nesting limit means the
    # parameter schema travels as a JSON string.
    serialized = []
    for tool in tools:
        row = tool.model_dump()
        if row.get('parameters') is not None:
            row['parameters'] = json.dumps(row['parameters'])
        serialized.append(row)
    _tools_cache.update(at=now, tools=serialized)
    return serialized


def _app_document(
    uid: str, settings: Dict[str, str], tools: List[Dict[str, Any]], user: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        'id': app_id_for(uid),
        'name': APP_NAME,
        'description': 'Your family calendar, tasks, chores and milestones (ALLGlance gateway).',
        'image': '',
        'uid': uid,
        'author': user.get('display_name', ''),
        'email': user.get('email', ''),
        'private': True,
        'approved': True,
        'status': 'approved',
        'category': 'utilities-and-tools',
        'capabilities': ['chat'],
        'created_at': datetime.now(timezone.utc),
        'external_integration': {
            'mcp_server_url': settings['mcp_url'],
            **(
                {'mcp_oauth_tokens': {'access_token': settings['token'], 'token_type': 'static'}}
                if settings['token']
                else {}
            ),
        },
        'chat_tools': tools,
        'bundled': True,
    }


def ensure_chat_app(uid: str, settings: Dict[str, str], tools: List[Dict[str, Any]]) -> bool:
    """Create and enable the user's ALLGlance chat app once; True when it was created now."""
    app_id = app_id_for(uid)
    existing = apps_db.get_app_by_id_db(app_id)
    if existing:
        # Keep the server address and token current when the stack's env changes.
        ext = existing.get('external_integration') or {}
        current = _app_document(uid, settings, tools, existing)['external_integration']
        if ext.get('mcp_server_url') != current['mcp_server_url'] or ext.get('mcp_oauth_tokens') != current.get(
            'mcp_oauth_tokens'
        ):
            apps_db.update_app_in_db({**existing, 'external_integration': current, 'chat_tools': tools})
        redis_db.enable_app(uid, app_id)
        return False
    user = users_db.get_user_profile(uid) or {}
    apps_db.add_app_to_db(_app_document(uid, settings, tools, user))
    redis_db.enable_app(uid, app_id)
    return True


def ensure_task_integration(uid: str, settings: Dict[str, str]) -> bool:
    """Seed the "allglance" task integration from env unless the user saved their own;
    make it the default when they have none. True when it was seeded now."""
    stored = users_db.get_task_integration(uid, APP_KEY)
    seeded = False
    if not stored or stored.get('seeded_from_env'):
        wanted = {
            'connected': True,
            'access_token': settings['token'],
            'base_url': settings['url'],
            'source': 'vikunja',
            'seeded_from_env': True,
        }
        if not stored or any(stored.get(k) != v for k, v in wanted.items()):
            users_db.set_task_integration(uid, APP_KEY, wanted)
            seeded = stored is None
    if not users_db.get_default_task_integration(uid):
        users_db.set_default_task_integration(uid, APP_KEY)
    return seeded


async def ensure_for_user(
    uid: str, settings: Optional[Dict[str, str]] = None, *, now: Optional[float] = None
) -> Dict[str, Any]:
    """Everything this module provides for one user. Safe to call repeatedly."""
    settings = settings or settings_from_env()
    if not settings:
        return {'configured': False}
    from utils.executors import db_executor, run_blocking

    import time

    now = time.time() if now is None else now
    outcome: Dict[str, Any] = {'configured': True, 'chat_app': False, 'task_integration': False}
    tools = await _discover_tools(settings, now=now)
    if tools:
        outcome['chat_app'] = await run_blocking(db_executor, ensure_chat_app, uid, settings, tools)
        outcome['tools'] = len(tools)
    outcome['task_integration'] = await run_blocking(db_executor, ensure_task_integration, uid, settings)
    return outcome


async def tick() -> None:
    """Hourly (utils/other/local_scheduler.py): wire every active member."""
    settings = settings_from_env()
    if not settings:
        return
    import database.day_bundle_users as active_users_db
    from utils.executors import db_executor, run_blocking

    for uid in await run_blocking(db_executor, active_users_db.active_users):
        try:
            await ensure_for_user(uid, settings)
        except Exception:  # noqa: BLE001 — one member must not stop the rest
            logger.exception('allglance: wiring failed uid=%s', uid)
