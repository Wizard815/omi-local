"""Outbound integrations (omi-local): where the user lets Omi send their things.

Targets and what each is for:

- wiki (LLMWIKI, the family Obsidian vault's MCP bridge): approved day summaries and chosen
  conversations become sources in the user's own folder (utils/wiki_outbound.py);
- odysseus (self-hosted AI workspace): simple asks, one chat turn (utils/agent_jobs.py);
- hermes (Hermes Agent's API server): agentic jobs as runs, their approvals answered in Omi;
- studio (Ekko / Hermes Studio): coding agents through /api/studio/orb/work, with a Studio key
  made for Omi (``sk-studio-omi-…``, Studio: Connections → Omi); Omi also lists and answers the
  Agent work approvals in Omi's Studio chats (utils/agent_jobs.py);
- coinpile: a money note or transaction, which Coinpile asks about again on its side;
- orb (Pondering Orb): an optional log line when a day is approved.

Nothing is sent on its own. A target is off until the user turns it on; then a day summary
goes out only when the user approves the day (auto-approve counts only where the user allowed
it for that target: ``require_manual_approval`` off), and everything else only on an explicit
tap after a preview.

Each target can take its address (and, for Hermes and Odysseus, a household token) from the
stack's env (omi-host/docker-compose.yml): LLMWIKI_URL, HERMES_API_URL / HERMES_API_KEY,
ODYSSEUS_URL / ODYSSEUS_API_TOKEN, STUDIO_URL / STUDIO_API_KEY (+ STUDIO_WEBHOOK_SECRET for
results), COINPILE_URL, ORB_URL. The user's own address, token and webhook secret (Settings >
Connections) win over the stack's. Tokens and secrets are never logged or returned.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import database.integrations_out as store
from utils.executors import db_executor, run_blocking
from utils.outbound_http import OutboundError, mcp_call, mcp_ping, request_ok

logger = logging.getLogger(__name__)

TARGETS = store.TARGETS
ENV: Dict[str, Tuple[str, Optional[str]]] = {
    'wiki': ('LLMWIKI_URL', None),
    'hermes': ('HERMES_API_URL', 'HERMES_API_KEY'),
    'odysseus': ('ODYSSEUS_URL', 'ODYSSEUS_API_TOKEN'),
    'studio': ('STUDIO_URL', 'STUDIO_API_KEY'),
    'coinpile': ('COINPILE_URL', None),
    'orb': ('ORB_URL', None),
}
STUDIO_WEBHOOK_SECRET_ENV = 'STUDIO_WEBHOOK_SECRET'
STUDIO_AGENTS = ('ekko-agent', 'hermes', 'claude-code', 'codex', 'pi', 'grok', 'opencode')
KEPT_MODES = ('off', 'ask', 'on_keep')


def _env(name: Optional[str]) -> str:
    return os.getenv(name, '').strip() if name else ''


def clean_url(value: str) -> str:
    url = str(value or '').strip().rstrip('/')
    if url and not url.startswith(('http://', 'https://')):
        raise ValueError('The address must start with http:// or https://')
    return url


def effective(uid: str) -> Dict[str, Dict[str, Any]]:
    """Every target's settings as a sender uses them: the user's choices, their address and
    token (plain), else the stack's env defaults."""
    raw = store.get_raw(uid)
    out: Dict[str, Dict[str, Any]] = {}
    for target in TARGETS:
        row = dict(raw[target])
        url_env, token_env = ENV[target]
        own_url = str(row.get('url') or '').strip().rstrip('/')
        own_token = store.secret(uid, row, 'token')
        row['url'] = own_url or _env(url_env).rstrip('/')
        row['url_from_env'] = not own_url and bool(row['url'])
        row['token'] = own_token or _env(token_env)
        row['token_from_env'] = not own_token and bool(row['token'])
        if target == 'studio':
            row['webhook_secret'] = store.secret(uid, row, 'webhook_secret') or _env(STUDIO_WEBHOOK_SECRET_ENV)
        for key in [k for k in row if k.endswith('_enc')]:
            row.pop(key)
        out[target] = row
    return out


def ready(settings: Dict[str, Any]) -> bool:
    """On, with an address and a token (every target here needs one)."""
    return bool(settings.get('enabled')) and bool(settings.get('url')) and bool(settings.get('token'))


def target(uid: str, name: str) -> Optional[Dict[str, Any]]:
    settings = effective(uid)[name]
    return settings if ready(settings) else None


def same_host(a: str, b: str) -> bool:
    """Same scheme-less host:port (how the dev-settings webhook is matched to the wiki)."""
    pa, pb = urlparse(a or ''), urlparse(b or '')
    if not pa.hostname or not pb.hostname:
        return False
    port_a = pa.port or (443 if pa.scheme == 'https' else 80)
    port_b = pb.port or (443 if pb.scheme == 'https' else 80)
    return pa.hostname.lower() == pb.hostname.lower() and port_a == port_b


def wiki_owns_conversation_webhook(uid: str, webhook_url: str) -> bool:
    """The developer Conversation Events webhook points at the wiki Omi now sends to itself:
    while Send to wiki is on, that webhook is skipped so nothing is filed twice (and nothing
    unapproved is filed at all)."""
    try:
        wiki = effective(uid)['wiki']
    except Exception:  # noqa: BLE001 — settings unreadable: the webhook behaves as before
        return False
    return bool(wiki.get('enabled')) and bool(wiki.get('url')) and same_host(webhook_url, wiki['url'])


def present(uid: str, *, conversation_webhook_url: str = '') -> Dict[str, Any]:
    """The settings for the app: no secrets, only whether each is set."""
    raw = store.get_raw(uid)
    settings = effective(uid)
    out: Dict[str, Any] = {}
    for name in TARGETS:
        row = settings[name]
        shown = {
            k: v
            for k, v in row.items()
            if k not in ('token', 'webhook_secret', 'updated_at') and not k.endswith('_enc')
        }
        shown['has_token'] = bool(row.get('token'))
        shown['own_url'] = str(raw[name].get('url') or '')
        if name == 'studio':
            shown['has_webhook_secret'] = bool(row.get('webhook_secret'))
            shown['webhook_path'] = '/v1/integrations/studio/events'
        out[name] = shown
    out['conversation_webhook_to_wiki'] = bool(
        conversation_webhook_url
        and settings['wiki'].get('url')
        and same_host(conversation_webhook_url, settings['wiki']['url'])
    )
    return out


async def test_connection(uid: str, name: str) -> Tuple[bool, str, str]:
    """A functional probe with the effective settings: (ok, error, what was checked)."""
    settings = (await run_blocking(db_executor, effective, uid))[name]
    if not settings.get('url'):
        return False, 'No address set', ''
    if not settings.get('token'):
        return False, 'No token set', ''
    url, token = settings['url'], settings['token']
    auth = {'Authorization': f'Bearer {token}'}
    try:
        if name == 'wiki':
            text = await mcp_call(f'{url}/family-mcp', token, 'whoami', {})
            detail = text.splitlines()[0][:200] if text else 'family-mcp answered'
        elif name == 'hermes':
            await request_ok('GET', f'{url}/v1/models', headers=auth)
            detail = 'Hermes API server answered /v1/models'
        elif name == 'odysseus':
            await request_ok('GET', f'{url}/api/models', headers=auth)
            detail = 'Odysseus answered /api/models with this token (chat scope)'
        elif name == 'studio':
            body = await request_ok('GET', f'{url}/api/studio/orb/work', headers=auth)
            count = len((body or {}).get('work') or []) if isinstance(body, dict) else 0
            detail = f'Studio answered /api/studio/orb/work ({count} work items)'
        elif name == 'coinpile':
            await mcp_ping(f'{url}/api/v1/mcp', token)
            detail = 'Coinpile MCP answered'
        else:
            await mcp_ping(f'{url}/mcp', token)
            detail = 'Pondering Orb MCP answered'
    except OutboundError as error:
        return False, error.message, ''
    return True, '', detail
