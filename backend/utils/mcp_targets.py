"""Coinpile and Pondering Orb as Connections (omi-local): both are MCP servers Omi calls with
the person's token.

- Coinpile (Wizard815/coinpile, ``POST {url}/api/v1/mcp``, a ``cp_<person>_...`` token with the
  mcp scope): ``write_note {title, body}`` or ``record_transaction {account_id, amount,
  description, date?, category?}``. Both only *ask*: Coinpile parks the change until the user
  answers Yes on its Agent page (src/coinpile/agent.py mcp_handle), a second OK on top of the
  explicit Send in Omi. ``list_accounts`` (read-only) feeds the account picker.
- Pondering Orb (Wizard815/pondering-orb, ``POST {url}/mcp``, an ``ORB_API_TOKENS`` token):
  ``orb_add_log {project, text}`` when a day is approved, if the user turned it on. Orb refuses
  text over its configured length (50 characters by default, server/src/tools.ts), so the line
  is cut to fit.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import database.action_items as action_items_db
import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.outbound_receipts as receipts_db
from utils import integrations_out, outbound_dispatch
from utils.executors import db_executor, run_blocking
from utils.outbound_http import OutboundError, mcp_call

logger = logging.getLogger(__name__)

ORB_LOG_MAX = 50


class TargetNotReady(Exception):
    pass


def _ready(uid: str, name: str) -> Dict[str, Any]:
    settings = integrations_out.effective(uid)[name]
    if not integrations_out.ready(settings):
        raise TargetNotReady(f'{name} is off, or its address or token is missing (Settings > Connections)')
    return settings


# ---- Pondering Orb ---------------------------------------------------------------------------


def orb_line(title: str) -> str:
    line = f'Omi day approved: {title}'.strip()
    return line if len(line) <= ORB_LOG_MAX else line[: ORB_LOG_MAX - 1].rstrip() + '…'


async def orb_log_day(uid: str, date_str: str) -> Dict[str, Any]:
    orb = await run_blocking(db_executor, _ready, uid, 'orb')
    review = await run_blocking(db_executor, day_reviews_db.get_day_review, uid, date_str) or {}
    text = orb_line(str(review.get('title') or date_str))
    key = f'orb_day_{date_str}'
    try:
        answer = await mcp_call(
            f"{orb['url']}/mcp", orb['token'], 'orb_add_log', {'project': orb.get('project') or 'General', 'text': text}
        )
        receipt = {
            'target': 'orb',
            'sent_at': datetime.now(timezone.utc),
            'title': text,
            'revision': 0,
            'detail': answer[:300],
            'error': '',
        }
    except OutboundError as error:
        receipt = {
            'target': 'orb',
            'sent_at': None,
            'title': text,
            'revision': 0,
            'detail': '',
            'error': error.message,
            'error_kind': error.kind,
        }
    rows = await run_blocking(db_executor, receipts_db.add, uid, key, receipt)
    await run_blocking(db_executor, outbound_dispatch.mirror, uid, date_str, 'orb', rows)
    return receipt


# ---- Coinpile --------------------------------------------------------------------------------


def coinpile_draft(uid: str, conversation_id: Optional[str], task_id: Optional[str]) -> Dict[str, str]:
    """A note's title and body from the chosen conversation or task (the app shows it, editable)."""
    if task_id:
        task = action_items_db.get_action_item(uid, task_id) or {}
        if not task:
            raise TargetNotReady('Task not found')
        return {'title': str(task.get('description') or 'Task')[:200], 'body': str(task.get('description') or '')}
    if conversation_id:
        row = conversations_db.get_conversation(uid, conversation_id) or {}
        if not row or row.get('is_locked'):
            raise TargetNotReady('Conversation not found')
        structured = row.get('structured') or {}
        title = str(structured.get('title') or 'Conversation')[:200]
        return {'title': title, 'body': str(structured.get('overview') or '')}
    return {'title': '', 'body': ''}


async def coinpile_accounts(uid: str) -> List[Dict[str, str]]:
    coinpile = await run_blocking(db_executor, _ready, uid, 'coinpile')
    text = await mcp_call(f"{coinpile['url']}/api/v1/mcp", coinpile['token'], 'list_accounts', {})
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = rows.get('accounts') or []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and (row.get('account_id') or row.get('id')):
            out.append(
                {
                    'id': str(row.get('account_id') or row.get('id')),
                    'name': str(row.get('name') or ''),
                    'type': str(row.get('type') or ''),
                }
            )
    return out


async def coinpile_send(uid: str, request: Dict[str, Any]) -> str:
    """The user's explicit Send to Coinpile. Returns Coinpile's answer (the change waits for a Yes)."""
    coinpile = await run_blocking(db_executor, _ready, uid, 'coinpile')
    if request.get('kind') == 'transaction':
        if not request.get('account_id') or request.get('amount') is None:
            raise TargetNotReady('A transaction needs an account and an amount')
        tool = 'record_transaction'
        arguments: Dict[str, Any] = {
            'account_id': request['account_id'],
            'amount': float(request['amount']),
            'description': (request.get('description') or request.get('title') or 'From Omi')[:500],
        }
        for optional in ('date', 'category'):
            if request.get(optional):
                arguments[optional] = request[optional]
    else:
        if not (request.get('title') or '').strip():
            raise TargetNotReady('A note needs a title')
        tool = 'write_note'
        arguments = {'title': request['title'], 'body': request.get('body') or ''}
    answer = await mcp_call(f"{coinpile['url']}/api/v1/mcp", coinpile['token'], tool, arguments)
    source = request.get('conversation_id') or request.get('task_id') or 'manual'
    receipt = {
        'target': 'coinpile',
        'sent_at': datetime.now(timezone.utc),
        'title': str(arguments.get('title') or arguments.get('description')),
        'revision': 0,
        'detail': answer[:300],
        'error': '',
    }
    await run_blocking(db_executor, receipts_db.add, uid, f'coinpile_{source}', receipt)
    return answer
