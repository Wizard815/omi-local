"""Send to wiki (omi-local): approved day summaries and chosen conversations become sources in
the user's LLMWIKI folder.

The wiki is the family Obsidian vault's MCP bridge (Wizard815/LLM-will-remember-this,
.obsidian/plugins/llmwiki-mcp-bridge/main.js). Omi calls its stateless JSON-RPC endpoint
``POST {url}/family-mcp`` with the person's family token (Bearer) and the tool
``memory_add_source {title, content, date, person?}``, which writes
``raw/sources/<date> - <title>.md`` (status unprocessed, for Ingest) and never overwrites.

Why not ``conversation_log`` / ``/family/conversation`` for a conversation: that path files a
transcript per speaker but drops the overview and the action items (main.js serveConversation
maps only title, date, id and segments). ``memory_add_source`` carries Omi's edited title,
overview and action items in markdown; the per-speaker filing is lost (an optional wiki-side
change could add overview/action_items to conversation_log).

A second copy of the same day or conversation (the day reopened and approved again, or the
user chose "send revised copy") is filed as "<title> (revised n)", because sources are never
changed. Every send is recorded (database/outbound_receipts.py); a day's receipts also show on
its review.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.notifications as notification_db
import database.outbound_receipts as receipts_db
from models.conversation import Conversation
from utils import day_review_service, integrations_out, outbound_dispatch
from utils.executors import db_executor, run_blocking
from utils.outbound_http import OutboundError, mcp_call

logger = logging.getLogger(__name__)

DAY_TITLE = 'Omi day review'
MAX_REVISION_TRIES = 10
MAX_CONTENT_CHARS = 200_000


class WikiNotReady(Exception):
    pass


def day_receipt_key(date_str: str) -> str:
    return f'wiki_day_{date_str}'


def conversation_receipt_key(conversation_id: str) -> str:
    return f'wiki_conversation_{conversation_id}'


def revised(title: str, revision: int) -> str:
    return title if revision <= 0 else f'{title} (revised {revision})'


def _sent(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if r.get('sent_at')]


def _local(value: Any, zone: Any) -> str:
    if not isinstance(value, datetime):
        return ''
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone).strftime('%Y-%m-%d %H:%M')


def _zone(uid: str):
    return day_review_service.timezone_for(notification_db.get_user_time_zone(uid))


# ---- what leaves -----------------------------------------------------------------------------


def day_markdown(uid: str, date_str: str) -> Tuple[str, str]:
    """(review title, markdown) of the approved day, exactly as it would be sent. Raises
    WikiNotReady when the day has no approved summary (a draft is never sent)."""
    stored = day_reviews_db.get_day_review(uid, date_str)
    if not stored or day_review_service.is_draft(stored):
        raise WikiNotReady('This day has no approved summary')
    review = day_review_service.present({**stored, 'date': date_str})
    zone = _zone(uid)
    day = day_review_service.get_day(uid, date_str, include_drafts=False)
    rows = {c['id']: c for c in day.get('conversations') or []}
    lines = [
        f"**From:** Omi day review of {date_str}, approved {_local(review.get('approved_at'), zone)}",
        '',
        f"# {review.get('title') or date_str}",
        '',
    ]
    sections = review.get('sections') or []
    if sections:
        for section in sections:
            lines += [f"## {section.get('title') or 'Part'}", '', str(section.get('text') or '').strip(), '']
            if section.get('tags'):
                lines += ['Tags: ' + ', '.join(f'#{t}' for t in section['tags']), '']
            ids = list(section.get('conversation_ids') or [])
            if ids:
                lines.append('Conversations:')
                for cid in ids:
                    row = rows.get(cid) or {}
                    label = row.get('title') or cid[:8]
                    lines.append(f"- {label} ({_local(row.get('started_at'), zone)[11:]}) omi://conversation/{cid}")
                lines.append('')
    elif review.get('final_text'):
        lines += [str(review['final_text']).strip(), '']
    kept = [rows[c] for c in review.get('kept_conversation_ids') or [] if c in rows]
    if kept:
        lines += ['## Kept conversations', '']
        for row in kept:
            overview = str(row.get('overview') or '').strip().replace('\n', ' ')
            lines.append(
                f"- **{row.get('title') or 'Conversation'}** ({_local(row.get('started_at'), zone)[11:]})"
                + (f': {overview}' if overview else '')
                + f" omi://conversation/{row['id']}"
            )
        lines.append('')
    return str(review.get('title') or ''), '\n'.join(lines).strip()[:MAX_CONTENT_CHARS]


def _conversation(uid: str, conversation_id: str) -> Conversation:
    row = conversations_db.get_conversation(uid, conversation_id)
    if not row or row.get('deleted'):
        raise WikiNotReady('Conversation not found')
    if row.get('is_locked'):
        raise WikiNotReady('This conversation is locked')
    return Conversation(**row)


def conversation_markdown(uid: str, conversation_id: str, *, include_transcript: bool) -> Tuple[str, str, str]:
    """(base title, local date, markdown) of a conversation as the user edited it."""
    conversation = _conversation(uid, conversation_id)
    zone = _zone(uid)
    structured = conversation.structured
    title = str(getattr(structured, 'title', '') or '').strip() or 'Conversation'
    started = conversation.started_at or conversation.created_at
    date_str = _local(started, zone)[:10] or datetime.now(timezone.utc).date().isoformat()
    lines = [
        f'**From:** Omi conversation, {_local(started, zone)}'
        + (f' to {_local(conversation.finished_at, zone)[11:]}' if conversation.finished_at else '')
        + f' (omi://conversation/{conversation.id})',
        '',
    ]
    overview = str(getattr(structured, 'overview', '') or '').strip()
    if overview:
        lines += ['## Overview', '', overview, '']
    items = [a for a in (getattr(structured, 'action_items', None) or []) if getattr(a, 'description', '')]
    if items:
        lines += ['## Action items', '']
        lines += [f"- [{'x' if getattr(a, 'completed', False) else ' '}] {a.description}" for a in items]
        lines.append('')
    if include_transcript:
        transcript = day_review_service._transcript(conversation).strip()  # noqa: SLF001 — same text the review reads
        if transcript:
            lines += ['## Transcript', '', transcript, '']
    base = f'Conversation - {title} ({conversation.id[:8]})'
    return base, date_str, '\n'.join(lines).strip()[:MAX_CONTENT_CHARS]


# ---- sending ---------------------------------------------------------------------------------


def _wiki(uid: str) -> Dict[str, Any]:
    wiki = integrations_out.effective(uid)['wiki']
    if not integrations_out.ready(wiki):
        raise WikiNotReady('Send to wiki is off, or its address or token is missing (Settings > Connections)')
    return wiki


async def _add_source(
    uid: str, wiki: Dict[str, Any], key: str, base: str, date_str: str, content: str
) -> Dict[str, Any]:
    """memory_add_source as the next revision; a taken title (a copy the receipts do not know
    about) moves on to the next revision. Records and returns the receipt."""
    previous = await run_blocking(db_executor, receipts_db.receipts, uid, key)
    revision = len(_sent(previous))
    arguments: Dict[str, Any] = {'date': date_str, 'content': content}
    if wiki.get('person'):
        arguments['person'] = wiki['person']
    last_error, last_kind = '', ''
    for _ in range(MAX_REVISION_TRIES):
        title = revised(base, revision)
        try:
            answer = await mcp_call(
                f"{wiki['url']}/family-mcp", wiki['token'], 'memory_add_source', {**arguments, 'title': title}
            )
        except OutboundError as error:
            if error.kind == 'refused' and 'already exists' in error.message:
                revision += 1
                continue
            last_error, last_kind = error.message, error.kind
            break
        receipt = {
            'target': 'wiki',
            'sent_at': datetime.now(timezone.utc),
            'title': title,
            'revision': revision,
            'detail': answer[:300],
            'error': '',
        }
        await run_blocking(db_executor, receipts_db.add, uid, key, receipt)
        return receipt
    else:
        last_error = 'The wiki already has too many copies of this'
    receipt = {
        'target': 'wiki',
        'sent_at': None,
        'title': revised(base, revision),
        'revision': revision,
        'detail': '',
        'error': last_error,
        'error_kind': last_kind,
    }
    await run_blocking(db_executor, receipts_db.add, uid, key, receipt)
    return receipt


async def send_day(uid: str, date_str: str) -> Dict[str, Any]:
    """Send the approved day summary now (the queue calls this once the Undo window passed)."""
    wiki = await run_blocking(db_executor, _wiki, uid)
    _, content = await run_blocking(db_executor, day_markdown, uid, date_str)
    receipt = await _add_source(uid, wiki, day_receipt_key(date_str), DAY_TITLE, date_str, content)
    rows = await run_blocking(db_executor, receipts_db.receipts, uid, day_receipt_key(date_str))
    await run_blocking(db_executor, outbound_dispatch.mirror, uid, date_str, 'wiki', rows)
    logger.info(
        'wiki_outbound: day uid=%s date=%s revision=%s ok=%s', uid, date_str, receipt['revision'], not receipt['error']
    )
    return receipt


async def preview_conversation(uid: str, conversation_id: str) -> Dict[str, Any]:
    def _build() -> Dict[str, Any]:
        wiki = integrations_out.effective(uid)['wiki']
        base, date_str, content = conversation_markdown(
            uid, conversation_id, include_transcript=bool(wiki.get('include_transcript'))
        )
        rows = receipts_db.receipts(uid, conversation_receipt_key(conversation_id))
        title = revised(base, len(_sent(rows)))
        where = f"{wiki.get('url') or '(no address)'} : raw/sources/{date_str} - {title}.md"
        return {'target': 'wiki', 'title': title, 'content': content, 'destination': where, 'receipts': rows}

    return await run_blocking(db_executor, _build)


async def send_conversation(
    uid: str, conversation_id: str, *, revised_copy: bool = False
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Send one conversation as the user edited it. Returns (receipt, already_sent): a second
    copy needs ``revised_copy`` (the app asks "already sent on ...; send a revised copy?")."""
    wiki = await run_blocking(db_executor, _wiki, uid)
    key = conversation_receipt_key(conversation_id)
    previous = await run_blocking(db_executor, receipts_db.receipts, uid, key)
    if _sent(previous) and not revised_copy:
        return None, True
    base, date_str, content = await run_blocking(
        db_executor,
        lambda: conversation_markdown(uid, conversation_id, include_transcript=bool(wiki.get('include_transcript'))),
    )
    receipt = await _add_source(uid, wiki, key, base, date_str, content)
    logger.info('wiki_outbound: conversation uid=%s ok=%s revision=%s', uid, not receipt['error'], receipt['revision'])
    return receipt, False
