"""Send to wiki (omi-local): approved day summaries and chosen conversations become sources in
the user's LLMWIKI folder.

The wiki is the family Obsidian vault's MCP bridge (Wizard815/LLM-will-remember-this,
.obsidian/plugins/llmwiki-mcp-bridge/main.js). Omi calls its stateless JSON-RPC endpoint
``POST {url}/family-mcp`` with the person's family token (Bearer) and the tool
``memory_add_source {title, content, date, person?}``, which writes
``raw/sources/<date> - <title>.md`` (status unprocessed, for Ingest) and never overwrites. A
day summary goes that way.

A conversation goes through ``conversation_log {title, id, date, source, overview,
action_items, segments?, wearer?}``: the wiki files it in the folder of each family member who
spoke (as far as the token may), with Omi's edited overview and action items above the
transcript. Each line carries the speaker's name when Omi knows the person, else Omi's label,
plus ``is_user`` and ``person_id`` (the registry's ``omi_people`` maps them). Without the
transcript (the default) only the overview and action items go, filed for the wearer. The
wiki names it ``Conversation - <title> (<id8>)``; a copy already there answers "Already
filed", and the next revision is tried.

A second copy of the same day or conversation (the day reopened and approved again, or the
user chose "send revised copy") is filed as "<title> (revised n)", because sources are never
changed. Every send is recorded (database/outbound_receipts.py); a day's receipts also show on
its review.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.notifications as notification_db
import database.outbound_receipts as receipts_db
import database.users as users_db
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


def wiki_conversation_title(title: str, conversation_id: str) -> str:
    """The name the wiki gives a conversation_log source (main.js: the id cut to 8 safe chars)."""
    short = re.sub(r'[^A-Za-z0-9-]', '', conversation_id)[:8]
    return f'Conversation - {title or "untitled"}' + (f' ({short})' if short else '')


class _Taken(Exception):
    """The wiki already has a source of that title: try the next revision."""


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
        transcript = day_review_service.transcript_text(conversation).strip()
        if transcript:
            lines += ['## Transcript', '', transcript, '']
    base = f'Conversation - {title} ({conversation.id[:8]})'
    return base, date_str, '\n'.join(lines).strip()[:MAX_CONTENT_CHARS]


def conversation_log_arguments(
    uid: str, conversation_id: str, *, include_transcript: bool, wearer: str = ''
) -> Tuple[str, Dict[str, Any]]:
    """(edited title, conversation_log arguments without the title) for one conversation."""
    conversation = _conversation(uid, conversation_id)
    structured = conversation.structured
    started = conversation.started_at or conversation.created_at
    date_str = _local(started, _zone(uid))[:10] or datetime.now(timezone.utc).date().isoformat()
    arguments: Dict[str, Any] = {'id': conversation.id, 'date': date_str, 'source': 'omi'}
    overview = str(getattr(structured, 'overview', '') or '').strip()
    if overview:
        arguments['overview'] = overview
    items = [
        {'description': str(a.description), 'completed': bool(getattr(a, 'completed', False))}
        for a in (getattr(structured, 'action_items', None) or [])
        if getattr(a, 'description', '')
    ]
    if items:
        arguments['action_items'] = items
    if include_transcript:
        segments = [s for s in (conversation.transcript_segments or []) if str(getattr(s, 'text', '') or '').strip()]
        ids = sorted({s.person_id for s in segments if getattr(s, 'person_id', None)})
        names = {p.get('id'): p.get('name') for p in (users_db.get_people_by_ids(uid, ids) if ids else []) if p}
        arguments['segments'] = [
            {
                'text': str(s.text),
                'speaker': names.get(s.person_id) or s.speaker or '',
                'is_user': bool(s.is_user),
                **({'person_id': s.person_id} if s.person_id else {}),
                **({'start': s.start} if getattr(s, 'start', None) is not None else {}),
            }
            for s in segments
        ]
    if wearer and wearer != 'household':
        arguments['wearer'] = wearer
    title = str(getattr(structured, 'title', '') or '').strip() or 'Conversation'
    return title, arguments


# ---- sending ---------------------------------------------------------------------------------


def _wiki(uid: str) -> Dict[str, Any]:
    wiki = integrations_out.effective(uid)['wiki']
    if not integrations_out.ready(wiki):
        raise WikiNotReady('Send to wiki is off, or its address or token is missing (Settings > Connections)')
    return wiki


async def _add_source(
    uid: str, wiki: Dict[str, Any], key: str, base: str, date_str: str, content: str
) -> Dict[str, Any]:
    """memory_add_source as the next revision. Records and returns the receipt."""
    arguments: Dict[str, Any] = {'date': date_str, 'content': content}
    if wiki.get('person'):
        arguments['person'] = wiki['person']

    async def attempt(revision: int) -> Tuple[str, str]:
        title = revised(base, revision)
        try:
            answer = await mcp_call(
                f"{wiki['url']}/family-mcp", wiki['token'], 'memory_add_source', {**arguments, 'title': title}
            )
        except OutboundError as error:
            if error.kind == 'refused' and 'already exists' in error.message:
                raise _Taken() from error
            raise
        return title, answer

    return await _file(uid, key, attempt, lambda revision: revised(base, revision))


async def _log_conversation(uid: str, wiki: Dict[str, Any], key: str, conversation_id: str) -> Dict[str, Any]:
    """conversation_log as the next revision. Records and returns the receipt."""
    base, arguments = await run_blocking(
        db_executor,
        lambda: conversation_log_arguments(
            uid,
            conversation_id,
            include_transcript=bool(wiki.get('include_transcript')),
            wearer=str(wiki.get('person') or ''),
        ),
    )

    def name(revision: int) -> str:
        return wiki_conversation_title(revised(base, revision), conversation_id)

    async def attempt(revision: int) -> Tuple[str, str]:
        answer = await mcp_call(
            f"{wiki['url']}/family-mcp",
            wiki['token'],
            'conversation_log',
            {**arguments, 'title': revised(base, revision)},
        )
        if answer.lstrip().startswith('Already filed'):
            raise _Taken()
        return name(revision), answer

    return await _file(uid, key, attempt, name)


async def _file(uid: str, key: str, attempt, name) -> Dict[str, Any]:
    """Send as the next revision; a taken title (a copy the receipts do not know about) moves on
    to the next revision. Records and returns the receipt."""
    previous = await run_blocking(db_executor, receipts_db.receipts, uid, key)
    revision = len(_sent(previous))
    last_error, last_kind = '', ''
    for _ in range(MAX_REVISION_TRIES):
        try:
            title, answer = await attempt(revision)
        except _Taken:
            revision += 1
            continue
        except OutboundError as error:
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
        'title': name(revision),
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
        _, date_str, content = conversation_markdown(
            uid, conversation_id, include_transcript=bool(wiki.get('include_transcript'))
        )
        conversation = _conversation(uid, conversation_id)
        edited = str(getattr(conversation.structured, 'title', '') or '').strip() or 'Conversation'
        rows = receipts_db.receipts(uid, conversation_receipt_key(conversation_id))
        title = wiki_conversation_title(revised(edited, len(_sent(rows))), conversation_id)
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
    receipt = await _log_conversation(uid, wiki, key, conversation_id)
    logger.info('wiki_outbound: conversation uid=%s ok=%s revision=%s', uid, not receipt['error'], receipt['revision'])
    return receipt, False
