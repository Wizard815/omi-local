"""End-of-day review orchestration: reads/writes conversations and day_reviews.

Rules (text handling, prompt, corrections) are in utils/day_review.py; this
module owns the order of side effects so a failure never loses a conversation:

finalize: generate the summary FIRST (nothing mutated if the model fails),
then persist the review, then star the kept conversations (enriching the ones
the hold kept raw, config/conversation_hold.py), merge each group into one new
conversation, and discard the dropped and rolled-up ones (tagged
``day_review_date``). ``reopen`` restores the dropped and rolled-up ones; a
merge deleted its sources and stays.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytz

import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.notifications as notification_db
from config import conversation_hold
from models.conversation import Conversation
from utils.conversations import lifecycle
from utils.conversations.factory import deserialize_conversation
from utils.conversations.processing_trigger import ProcessingTrigger
from utils.executors import postprocess_executor
from utils.day_review import (
    DEFAULT_MAX_INPUT_CHARS,
    MaterialItem,
    build_prompt,
    compose_final_text,
    parse_summary_output,
    redaction_quotes,
    render_material,
)
from utils.llm.clients import get_llm
from utils.llm.usage_tracker import Features, track_usage

DAY_REVIEW_DATE_FIELD = 'day_review_date'
_DAY_QUERY_LIMIT = 300
_SNIPPET_CHARS = 240


class DayReviewError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _request_timeout_seconds() -> float:
    """A long day on a local model can take minutes; the shared foreground deadline (60s) is too tight."""
    try:
        return float(os.getenv('DAY_REVIEW_TIMEOUT_SECONDS') or 240.0)
    except ValueError:
        return 240.0


def _max_input_chars() -> int:
    try:
        return int(os.getenv('DAY_REVIEW_MAX_INPUT_CHARS') or DEFAULT_MAX_INPUT_CHARS)
    except ValueError:
        return DEFAULT_MAX_INPUT_CHARS


def parse_date(value: str) -> date_cls:
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise DayReviewError(422, 'Invalid date format. Use YYYY-MM-DD')


def _tz(tz_name: Optional[str]):
    try:
        return pytz.timezone(tz_name) if tz_name else pytz.utc
    except pytz.UnknownTimeZoneError:
        return pytz.utc


def local_today(tz_name: Optional[str]) -> date_cls:
    return datetime.now(_tz(tz_name)).date()


def day_bounds_utc(day: date_cls, tz_name: Optional[str]) -> Tuple[datetime, datetime]:
    zone = _tz(tz_name)
    start = zone.localize(datetime.combine(day, datetime.min.time()))
    end = zone.localize(datetime.combine(day, datetime.max.time()))
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


@dataclass
class DayConversations:
    visible: List[Conversation]
    rolled_up: List[Conversation]
    dropped: List[Conversation]
    processing_count: int
    dropped_auto_count: int
    # Stored row fields the model does not carry, by conversation id.
    hold_rules: Dict[str, str]


def _status(conversation: Conversation) -> str:
    status = getattr(conversation, 'status', None)
    return str(getattr(status, 'value', status) or '')


def load_day(uid: str, day: date_cls, tz_name: Optional[str]) -> DayConversations:
    start, end = day_bounds_utc(day, tz_name)
    raw = conversations_db.get_conversations(
        uid,
        limit=_DAY_QUERY_LIMIT,
        include_discarded=True,
        start_date=start,
        end_date=end,
        date_field='started_at',
    )
    date_str = day.isoformat()
    review = day_reviews_db.get_day_review(uid, date_str) or {}
    dropped_ids = set(review.get('dropped_conversation_ids') or [])
    visible: List[Conversation] = []
    rolled: List[Conversation] = []
    dropped: List[Conversation] = []
    processing = 0
    dropped_auto = 0
    hold_rules: Dict[str, str] = {}
    for data in raw:
        if data.get('is_locked') or data.get('deleted'):
            continue
        conversation = deserialize_conversation(data)
        if data.get('discarded'):
            if data.get(DAY_REVIEW_DATE_FIELD) == date_str:
                (dropped if conversation.id in dropped_ids else rolled).append(conversation)
            else:
                dropped_auto += 1
            continue
        if _status(conversation) != 'completed':
            processing += 1
            continue
        if data.get('processing_deferred'):
            hold_rules[conversation.id] = str(data.get(conversation_hold.HOLD_RULE_FIELD) or '')
        visible.append(conversation)
    key = lambda c: c.started_at or c.created_at  # noqa: E731
    return DayConversations(
        sorted(visible, key=key),
        sorted(rolled, key=key),
        sorted(dropped, key=key),
        processing,
        dropped_auto,
        hold_rules,
    )


def _transcript(conversation: Conversation) -> str:
    try:
        return conversation.get_transcript(False)
    except Exception:
        return '\n'.join(
            str(getattr(s, 'text', '') or '')
            for s in (conversation.transcript_segments or [])
            if getattr(s, 'text', '')
        )


def _material_item(conversation: Conversation) -> MaterialItem:
    structured = conversation.structured
    return MaterialItem(
        started_at=conversation.started_at or conversation.created_at,
        finished_at=conversation.finished_at,
        title=str(getattr(structured, 'title', '') or ''),
        overview=str(getattr(structured, 'overview', '') or ''),
        action_items=[
            str(getattr(a, 'description', '') or '') for a in (getattr(structured, 'action_items', None) or [])
        ],
        transcript=_transcript(conversation),
    )


def generate_summary(
    uid: str,
    day: date_cls,
    tz_name: Optional[str],
    conversations: Sequence[Conversation],
    corrections: Sequence[Dict[str, Any]],
    previous_summary: Optional[str] = None,
) -> Tuple[str, str]:
    quotes = redaction_quotes(corrections)
    material = render_material(
        [_material_item(c) for c in conversations],
        redactions=quotes,
        max_chars=_max_input_chars(),
        tz=_tz(tz_name),
    )
    prompt = build_prompt(
        date_label=day.strftime('%A, %B %-d, %Y'),
        material=material,
        corrections=corrections,
        previous_summary=previous_summary,
    )
    try:
        with track_usage(uid, Features.DAILY_SUMMARY):
            response = get_llm('daily_summary', request_timeout=_request_timeout_seconds()).invoke(prompt)
    except Exception as e:
        raise DayReviewError(502, f'Could not write the day summary: {type(e).__name__}')
    content = getattr(response, 'content', response)
    if isinstance(content, list):
        content = ''.join(str(part.get('text', '')) if isinstance(part, dict) else str(part) for part in content)
    title, body = parse_summary_output(str(content or ''), redactions=quotes)
    if not body:
        raise DayReviewError(502, 'The model returned an empty day summary')
    return title, body


def _snippet(conversation: Conversation) -> str:
    text = ' '.join(str(getattr(s, 'text', '') or '') for s in (conversation.transcript_segments or []))
    return ' '.join(text.split())[:_SNIPPET_CHARS]


def _word_count(conversation: Conversation) -> int:
    return sum(len(str(getattr(s, 'text', '') or '').split()) for s in (conversation.transcript_segments or []))


def present(review: Dict[str, Any]) -> Dict[str, Any]:
    """Stored review + the derived reader-facing text."""
    corrections = list(review.get('corrections') or [])
    return {**review, 'final_text': compose_final_text(review.get('summary_text') or '', corrections)}


def get_day(uid: str, date_str: str) -> Dict[str, Any]:
    day = parse_date(date_str)
    tz_name = notification_db.get_user_time_zone(uid)
    loaded = load_day(uid, day, tz_name)
    review = day_reviews_db.get_day_review(uid, date_str)

    def row(conversation: Conversation, *, rolled_up: bool = False, dropped: bool = False) -> Dict[str, Any]:
        structured = conversation.structured
        held = conversation.id in loaded.hold_rules
        return {
            'id': conversation.id,
            'title': str(getattr(structured, 'title', '') or ''),
            'emoji': str(getattr(structured, 'emoji', '') or ''),
            'overview': str(getattr(structured, 'overview', '') or '')[:400],
            'started_at': conversation.started_at,
            'finished_at': conversation.finished_at,
            'starred': bool(getattr(conversation, 'starred', False)),
            'rolled_up': rolled_up,
            'dropped': dropped,
            'held': held,
            'hold_rule': loaded.hold_rules.get(conversation.id, ''),
            'transcript_snippet': _snippet(conversation),
            'word_count': _word_count(conversation),
        }

    rows = (
        [row(c) for c in loaded.visible]
        + [row(c, rolled_up=True) for c in loaded.rolled_up]
        + [row(c, dropped=True) for c in loaded.dropped]
    )
    rows.sort(key=lambda r: r['started_at'] or datetime.min.replace(tzinfo=timezone.utc))
    return {
        'date': date_str,
        'review': present(review) if review else None,
        'conversations': rows,
        'processing_count': loaded.processing_count,
        'dropped_auto_count': loaded.dropped_auto_count,
        'hold_enabled': conversation_hold.hold_enabled(),
    }


def _enrich_held(uid: str, conversation_id: str) -> None:
    """Run the full pipeline on a clip the hold kept raw. Background work: the
    review already answered, and the row shows as processing meanwhile."""
    from utils.conversations.process_conversation import process_conversation

    row = conversations_db.get_conversation(uid, conversation_id)
    if not row or not row.get('processing_deferred'):
        return
    try:
        process_conversation(
            uid, row.get('language') or 'en', Conversation(**row), trigger=ProcessingTrigger.FIRST_OPEN
        )
    except Exception as e:  # noqa: BLE001 — the row stays held and can be reprocessed
        import logging

        logging.getLogger(__name__).warning(
            'day review: enrichment failed uid=%s conv=%s %s', uid, conversation_id, type(e).__name__
        )


def _merge_group(uid: str, group: Sequence[str]) -> None:
    """Merge a clump into one new conversation; the merge runs the full pipeline (trigger MERGE)."""
    from utils.conversations.merge_conversations import perform_merge_async

    perform_merge_async(uid=uid, conversation_ids=list(group), reprocess=True)


def _submit(fn, *args) -> None:
    postprocess_executor.submit(fn, *args)


def finalize(
    uid: str,
    date_str: str,
    keep_ids: Sequence[str],
    merge_groups: Sequence[Sequence[str]] = (),
    drop_ids: Sequence[str] = (),
) -> Dict[str, Any]:
    day = parse_date(date_str)
    tz_name = notification_db.get_user_time_zone(uid)
    if day > local_today(tz_name):
        raise DayReviewError(422, 'Date cannot be in the future')
    if day_reviews_db.get_day_review(uid, date_str):
        raise DayReviewError(409, 'This day is already reviewed. Reopen it to sort it again.')

    loaded = load_day(uid, day, tz_name)
    if not loaded.visible:
        raise DayReviewError(400, f'No conversations to review for {date_str}')
    visible_ids = {c.id for c in loaded.visible}
    merge_groups = [list(dict.fromkeys(g)) for g in merge_groups]
    merged_ids = {cid for g in merge_groups for cid in g}
    chosen = [*keep_ids, *drop_ids, *merged_ids]
    unknown = [cid for cid in chosen if cid not in visible_ids]
    if unknown:
        raise DayReviewError(422, f'Not conversations of {date_str}: {", ".join(unknown[:5])}')
    if len(set(chosen)) != len(chosen):
        raise DayReviewError(422, 'A conversation can only be kept, merged or dropped once')
    if any(len(g) < 2 for g in merge_groups):
        raise DayReviewError(422, 'A merge group needs at least 2 conversations')
    keep = [c for c in loaded.visible if c.id in set(keep_ids)]
    dropped = [c for c in loaded.visible if c.id in set(drop_ids)]
    sorted_out = set(keep_ids) | set(drop_ids) | merged_ids
    rolled = [c for c in loaded.visible if c.id not in sorted_out]

    # Generate before mutating anything: a model failure leaves the day untouched.
    if rolled:
        title, body = generate_summary(uid, day, tz_name, rolled, [])
    else:
        title, body = 'Everything kept', ''

    now = datetime.now(timezone.utc)
    review = {
        'status': 'finalized',
        'title': title,
        'summary_text': body,
        'corrections': [],
        'kept_conversation_ids': [c.id for c in keep],
        'rolled_up_conversation_ids': [c.id for c in rolled],
        'merged_groups': [{'conversation_ids': list(g)} for g in merge_groups],
        'dropped_conversation_ids': [c.id for c in dropped],
        'time_zone': tz_name,
        'created_at': now,
        'updated_at': now,
        'generated_at': now if rolled else None,
        'generation_count': 1 if rolled else 0,
    }
    day_reviews_db.set_day_review(uid, date_str, review)
    for conversation in keep:
        if not getattr(conversation, 'starred', False):
            conversations_db.set_conversation_starred(uid, conversation.id, True)
        if conversation.id in loaded.hold_rules:
            _submit(_enrich_held, uid, conversation.id)
    for group in merge_groups:
        for cid in group:
            lifecycle.begin_merge(uid, cid)
        _submit(_merge_group, uid, group)
    for conversation in [*dropped, *rolled]:
        conversations_db.update_conversation(uid, conversation.id, {DAY_REVIEW_DATE_FIELD: date_str})
        lifecycle.discard(uid, conversation.id)
    return present({**review, 'date': date_str})


def _require_review(uid: str, date_str: str) -> Dict[str, Any]:
    parse_date(date_str)
    review = day_reviews_db.get_day_review(uid, date_str)
    if not review:
        raise DayReviewError(404, 'This day has not been reviewed yet')
    return review


def add_correction(uid: str, date_str: str, correction: Dict[str, Any]) -> Dict[str, Any]:
    review = _require_review(uid, date_str)
    summary = review.get('summary_text') or ''
    if correction['type'] in ('highlight', 'redaction'):
        quote = str(correction.get('quote') or '')
        start, end = correction.get('start'), correction.get('end')
        if start is not None and end is not None and summary[start:end] != quote:
            # The client selected on an older version of the text; keep the
            # quote (it re-anchors by content) but drop offsets that lie.
            correction['start'] = correction['end'] = None
    entry = {
        'id': uuid.uuid4().hex[:12],
        'type': correction['type'],
        'quote': correction.get('quote'),
        'start': correction.get('start'),
        'end': correction.get('end'),
        'text': correction.get('text'),
        'after_paragraph': correction.get('after_paragraph'),
        'created_at': datetime.now(timezone.utc),
        'applied_at': None,
    }
    review['corrections'] = list(review.get('corrections') or []) + [entry]
    review['updated_at'] = datetime.now(timezone.utc)
    day_reviews_db.set_day_review(uid, date_str, review)
    return present(review)


def delete_correction(uid: str, date_str: str, correction_id: str) -> Dict[str, Any]:
    review = _require_review(uid, date_str)
    corrections = list(review.get('corrections') or [])
    remaining = [c for c in corrections if c.get('id') != correction_id]
    if len(remaining) == len(corrections):
        raise DayReviewError(404, 'Correction not found')
    review['corrections'] = remaining
    review['updated_at'] = datetime.now(timezone.utc)
    day_reviews_db.set_day_review(uid, date_str, review)
    return present(review)


def regenerate(uid: str, date_str: str) -> Dict[str, Any]:
    """Rewrite the summary with every correction as ground truth."""
    review = _require_review(uid, date_str)
    rolled_ids = list(review.get('rolled_up_conversation_ids') or [])
    if not rolled_ids:
        raise DayReviewError(400, 'Nothing was rolled up for this day, so there is no summary to rewrite')
    raw = conversations_db.get_conversations_by_id(uid, rolled_ids, include_discarded=True)
    conversations = [deserialize_conversation(c) for c in raw if c]
    if not conversations:
        raise DayReviewError(410, 'The rolled-up conversations no longer exist')
    corrections = list(review.get('corrections') or [])
    title, body = generate_summary(
        uid,
        parse_date(date_str),
        review.get('time_zone') or notification_db.get_user_time_zone(uid),
        conversations,
        corrections,
        previous_summary=review.get('summary_text') or None,
    )
    now = datetime.now(timezone.utc)
    for c in corrections:
        # The model folded paragraphs/highlights into the new text; redactions
        # are never "applied" — they stay enforced on every future version.
        if c.get('type') != 'redaction' and not c.get('applied_at'):
            c['applied_at'] = now
    review.update(
        {
            'title': title,
            'summary_text': body,
            'corrections': corrections,
            'updated_at': now,
            'generated_at': now,
            'generation_count': int(review.get('generation_count') or 0) + 1,
        }
    )
    day_reviews_db.set_day_review(uid, date_str, review)
    return present(review)


def reopen(uid: str, date_str: str) -> Tuple[int, int]:
    """Undo a review: restore the rolled-up and dropped conversations and delete
    the review. Returns (restored, merged groups that stay merged)."""
    review = _require_review(uid, date_str)
    restored = 0
    hidden = [*(review.get('rolled_up_conversation_ids') or []), *(review.get('dropped_conversation_ids') or [])]
    for conversation_id in hidden:
        if not conversations_db.get_conversation(uid, conversation_id):
            continue
        lifecycle.restore_discarded(uid, conversation_id)
        conversations_db.update_conversation(uid, conversation_id, {DAY_REVIEW_DATE_FIELD: None})
        restored += 1
    day_reviews_db.delete_day_review(uid, date_str)
    return restored, len(review.get('merged_groups') or [])


def pending_dates(uid: str, days: int = 7) -> Dict[str, Any]:
    """Recent local dates (oldest first, today included) with unsorted completed conversations and no review."""
    tz_name = notification_db.get_user_time_zone(uid)
    today = local_today(tz_name)
    first = today - timedelta(days=max(0, days - 1))
    start, _ = day_bounds_utc(first, tz_name)
    _, end = day_bounds_utc(today, tz_name)
    raw = conversations_db.get_conversations(
        uid, limit=1000, include_discarded=False, start_date=start, end_date=end, date_field='started_at'
    )
    zone = _tz(tz_name)
    dates = set()
    for data in raw:
        if data.get('is_locked') or data.get('deleted') or str(data.get('status') or '') != 'completed':
            continue
        started = data.get('started_at') or data.get('created_at')
        if isinstance(started, datetime):
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            dates.add(started.astimezone(zone).date().isoformat())
    reviewed = {r.get('date') for r in day_reviews_db.list_day_reviews(uid, limit=max(days, 1) + 7)}
    return {'today': today.isoformat(), 'pending_dates': sorted(d for d in dates if d not in reviewed)}


def list_reviews(uid: str, limit: int = 30) -> List[Dict[str, Any]]:
    return [present(r) for r in day_reviews_db.list_day_reviews(uid, limit=limit)]
