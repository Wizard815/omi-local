"""End-of-day review orchestration: reads/writes conversations and day_reviews.

Rules (text handling, prompt, corrections) are in utils/day_review.py; this
module owns the order of side effects so a failure never loses a conversation:

finalize: generate the summary FIRST (nothing mutated if the model fails),
then persist the review, then star the kept conversations (enriching the ones
the hold kept raw, config/conversation_hold.py), merge each group into one new
conversation, and discard the dropped and rolled-up ones (tagged
``day_review_date``). ``reopen`` restores the dropped and rolled-up ones; a
merge deleted its sources and stays.

The summary is written in sections, each naming the conversations it came
from (utils/day_review.parse_sections). ``favorite_section`` pulls a section's
conversations back out of the bundle as starred conversations. The model's
per-section tags land on those conversations (utils/conversations/tags.py).

Nothing is approved for the user. ``prepare_draft`` (``bundle``; the overnight
job in utils/day_bundle_scheduler.py runs it) writes the summary of the
hands-off split (starred clips stay, everything else rolled up) as a review
with ``status = 'draft'``: no conversation is touched and nothing goes to the
calendar. The user edits the draft (corrections, regenerate, favorite a
section) and approves it (``approve``, or ``finalize`` with their own sort),
which finalizes using the draft's text when the split is the one it was
written for (no second model call). Only a finalized day reaches CalDAV;
``reopen`` turns it back into a draft, so its summary comes off the calendar
until it is approved again.

The user can also edit the text by hand (``edit_text``: the title and each
section), on a draft or an approved day; an approved day's summary on the
calendar follows. A rewrite by the model (``regenerate``, or approving a draft
whose conversations changed since) would replace those edits, so it refuses
with 409 until the user confirms (``replace_edits``).
"""

from __future__ import annotations

from utils.caldav import sync as caldav_sync
from utils import outbound_dispatch
import os
import uuid
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytz

import database.conversations as conversations_db
import database.day_bundle_users as day_bundle_users_db
import database.day_reviews as day_reviews_db
import database.notifications as notification_db
from config import conversation_hold
from models.conversation import Conversation
from utils.conversations import lifecycle
from utils.conversations.factory import deserialize_conversation
from utils.conversations.processing_trigger import ProcessingTrigger
from utils.executors import postprocess_executor
from utils.conversations.tags import normalize_tags
from utils.day_review import (
    DEFAULT_MAX_INPUT_CHARS,
    MaterialItem,
    build_prompt,
    compose_final_text,
    join_paragraphs,
    parse_sections,
    redaction_quotes,
    render_material,
    sections_to_rows,
    split_paragraphs,
)
from utils.llm.clients import get_llm
from utils.llm.usage_tracker import Features, track_usage

DAY_REVIEW_DATE_FIELD = 'day_review_date'
DRAFT = 'draft'
FINALIZED = 'finalized'
EDITED_AT = 'edited_by_user_at'
# The text as the model last wrote it, kept while the user's edits stand: an edit that puts it
# back (Undo) is no edit any more. Stored only; the API never returns it.
GENERATED_VERSION = 'generated_version'
EDITS_WOULD_BE_REPLACED = 'Rewriting replaces the text you edited. Confirm to rewrite it anyway.'
# An approval from a draft keeps what it changed, so Approve all's Undo can put the draft back
# exactly (its split, corrections, text and edits) without a model call. Stored only.
UNDO_APPROVAL = 'undo_approval'
UNDO_APPROVAL_WINDOW = timedelta(minutes=10)
_HIDDEN_FIELDS = (GENERATED_VERSION, UNDO_APPROVAL)
_DAY_QUERY_LIMIT = 300
_SNIPPET_CHARS = 240


def is_draft(review: Optional[Dict[str, Any]]) -> bool:
    return bool(review) and str((review or {}).get('status') or FINALIZED) == DRAFT


def has_user_edits(review: Optional[Dict[str, Any]]) -> bool:
    return bool((review or {}).get(EDITED_AT))


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


def timezone_for(tz_name: Optional[str]):
    try:
        return pytz.timezone(tz_name) if tz_name else pytz.utc
    except pytz.UnknownTimeZoneError:
        return pytz.utc


_tz = timezone_for


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


def transcript_text(conversation: Conversation) -> str:
    """A conversation's transcript as the review reads it (also what a Connection sends, when asked)."""
    return _transcript(conversation)


def _material_item(conversation: Conversation, ref: str = '') -> MaterialItem:
    structured = conversation.structured
    return MaterialItem(
        ref=ref,
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
) -> Tuple[str, str, List[Dict[str, Any]]]:
    """(title, body, section rows) for the rolled-up conversations, in day order."""
    quotes = redaction_quotes(corrections)
    key = lambda c: (c.started_at or c.created_at) or datetime.min.replace(tzinfo=timezone.utc)  # noqa: E731
    ordered = sorted(conversations, key=key)
    refs = [f'c{i}' for i in range(1, len(ordered) + 1)]
    ref_to_id = dict(zip(refs, (c.id for c in ordered)))
    material = render_material(
        [_material_item(c, ref) for c, ref in zip(ordered, refs)],
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
    title, body, sections = parse_sections(str(content or ''), refs, redactions=quotes)
    if not body:
        raise DayReviewError(502, 'The model returned an empty day summary')
    return title, body, sections_to_rows(sections, ref_to_id)


def _snippet(conversation: Conversation) -> str:
    text = ' '.join(str(getattr(s, 'text', '') or '') for s in (conversation.transcript_segments or []))
    return ' '.join(text.split())[:_SNIPPET_CHARS]


def _word_count(conversation: Conversation) -> int:
    return sum(len(str(getattr(s, 'text', '') or '').split()) for s in (conversation.transcript_segments or []))


def present(review: Dict[str, Any]) -> Dict[str, Any]:
    """Stored review + the derived reader-facing text."""
    corrections = list(review.get('corrections') or [])
    quotes = redaction_quotes(corrections)
    sections = []
    for row in review.get('sections') or []:
        sections.append(
            {
                **row,
                'title': _apply_quotes(str(row.get('title') or ''), quotes),
                'text': _apply_quotes(str(row.get('text') or ''), quotes),
            }
        )
    shown = {k: v for k, v in review.items() if k not in _HIDDEN_FIELDS}
    return {
        **shown,
        # The API keeps status 'finalized' (all a released app decodes) and says draft-ness in
        # `draft`; the stored document keeps status 'draft' until the day is approved.
        'status': FINALIZED,
        'draft': is_draft(review),
        'sections': sections,
        'tags': sorted({t for row in sections for t in (row.get('tags') or [])}),
        'final_text': compose_final_text(review.get('summary_text') or '', corrections),
    }


def _apply_quotes(text: str, quotes: Sequence[str]) -> str:
    from utils.day_review import apply_redactions

    return apply_redactions(text, quotes)


def _apply_section_tags(
    uid: str, conversations: Sequence[Conversation], sections: Sequence[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """The model's section tags land on the conversations the section came from, on top of the user's own.
    Returns the tags each changed conversation had before."""
    by_id = {c.id: c for c in conversations}
    previous: Dict[str, List[str]] = {}
    for row in sections:
        tags = list(row.get('tags') or [])
        if not tags:
            continue
        for cid in row.get('conversation_ids') or []:
            conversation = by_id.get(cid)
            if conversation is None:
                continue
            before = list(getattr(conversation, 'tags', None) or [])
            merged = normalize_tags([*before, *tags])
            if merged != before:
                try:
                    conversations_db.set_conversation_tags(uid, cid, merged)
                    previous.setdefault(cid, before)
                except Exception as error:  # noqa: BLE001 — tags are a bonus; the bundle stands without them
                    import logging

                    logging.getLogger(__name__).warning('day review: tagging %s failed: %s', cid, type(error).__name__)
    return previous


def get_day(uid: str, date_str: str, *, include_drafts: bool = True) -> Dict[str, Any]:
    """The day and its review. ``include_drafts=False`` (what a released app gets) shows a draft
    day as not reviewed yet: the app offers sorting, and its finalize approves the day."""
    day = parse_date(date_str)
    tz_name = notification_db.get_user_time_zone(uid)
    day_bundle_users_db.mark_active(uid)
    loaded = load_day(uid, day, tz_name)
    review = day_reviews_db.get_day_review(uid, date_str)
    if review and is_draft(review) and not include_drafts:
        review = None

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
            'tags': list(getattr(conversation, 'tags', None) or []),
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
    *,
    replace_edits: bool = False,
    approved_by: str = outbound_dispatch.APPROVED_BY_USER,
) -> Dict[str, Any]:
    day = parse_date(date_str)
    tz_name = notification_db.get_user_time_zone(uid)
    if day > local_today(tz_name):
        raise DayReviewError(422, 'Date cannot be in the future')
    existing = day_reviews_db.get_day_review(uid, date_str)
    if existing and not is_draft(existing):
        raise DayReviewError(409, 'This day is already reviewed. Reopen it to sort it again.')
    draft = existing if is_draft(existing) else None

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

    now = datetime.now(timezone.utc)
    corrections = list((draft or {}).get('corrections') or [])
    reused = draft_fits(draft, [c.id for c in rolled])
    if not reused and rolled and has_user_edits(draft) and not replace_edits:
        # The conversations changed since the user edited the draft: writing it for this split
        # would throw their edits away, so ask first.
        raise DayReviewError(409, EDITS_WOULD_BE_REPLACED)
    # Generate before mutating anything: a model failure leaves the day (and its draft) untouched.
    if reused and draft is not None:
        title = str(draft.get('title') or '')
        body = str(draft.get('summary_text') or '')
        sections = list(draft.get('sections') or [])
        generated_at, generation_count = draft.get('generated_at'), int(draft.get('generation_count') or 0)
    elif rolled:
        # The user's sort differs from the draft's: write it for this split, with the draft's
        # corrections as ground truth and its text as the previous version.
        title, body, sections = generate_summary(
            uid,
            day,
            tz_name,
            rolled,
            corrections,
            previous_summary=(draft or {}).get('summary_text') or None,
        )
        for c in corrections:
            if c.get('type') != 'redaction' and not c.get('applied_at'):
                c['applied_at'] = now
        generated_at, generation_count = now, int((draft or {}).get('generation_count') or 0) + 1
    else:
        title, body, sections = 'Everything kept', '', []
        generated_at, generation_count = None, 0

    review = {
        'status': FINALIZED,
        'title': title,
        'summary_text': body,
        'sections': sections,
        'corrections': corrections,
        'kept_conversation_ids': [c.id for c in keep],
        'rolled_up_conversation_ids': [c.id for c in rolled],
        'merged_groups': [{'conversation_ids': list(g)} for g in merge_groups],
        'dropped_conversation_ids': [c.id for c in dropped],
        'time_zone': tz_name,
        'created_at': (draft or {}).get('created_at') or now,
        'updated_at': now,
        'approved_at': now,
        'generated_at': generated_at,
        'generation_count': generation_count,
    }
    if reused and draft is not None and has_user_edits(draft):
        # Approved as the user edited it: the edits stand (and a later rewrite still asks).
        review[EDITED_AT] = draft.get(EDITED_AT)
        if draft.get(GENERATED_VERSION):
            review[GENERATED_VERSION] = draft.get(GENERATED_VERSION)
    newly_starred = [c.id for c in keep if not getattr(c, 'starred', False)]
    undo: Optional[Dict[str, Any]] = None
    if draft is not None and not merge_groups:
        # Approved from a draft: remember the draft as it was, for Approve all's Undo.
        undo = {
            'draft': {k: v for k, v in draft.items() if k not in ('date', UNDO_APPROVAL)},
            'starred': newly_starred,
            'tags': {},
            'at': now,
        }
        review[UNDO_APPROVAL] = undo
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
    retagged = _apply_section_tags(uid, rolled, sections)
    if undo is not None and retagged:
        undo['tags'] = retagged
        day_reviews_db.set_day_review(uid, date_str, review)
    caldav_sync.mark_dirty(uid)
    # The user's OK: queue what their Connections send for this day (the wiki, Orb). Sent
    # after the Undo window, and only while this approval stands (utils/outbound_worker.py).
    outbound_dispatch.day_approved(uid, date_str, review, approved_by)
    return present({**review, 'date': date_str})


def undo_approval(uid: str, date_str: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Approve all's Undo for a day: put the draft back exactly as it was before the approval
    (split, corrections, text and hand edits; no model call). The rolled-up and dropped
    conversations come back, the stars and tags the approval added come off, and the summary
    comes off the calendar. Only for an approval made from a draft in the last few minutes;
    a draft (already undone) is returned as it is."""
    review = _require_review(uid, date_str)
    if is_draft(review):
        return present(review)
    undo = review.get(UNDO_APPROVAL) or {}
    at = undo.get('at')
    now = now or datetime.now(timezone.utc)
    if not undo.get('draft') or not isinstance(at, datetime) or now - _aware(at) > UNDO_APPROVAL_WINDOW:
        raise DayReviewError(409, 'This approval can no longer be undone here; reopen the day instead')
    for conversation_id in [
        *(review.get('rolled_up_conversation_ids') or []),
        *(review.get('dropped_conversation_ids') or []),
    ]:
        if not conversations_db.get_conversation(uid, conversation_id):
            continue
        lifecycle.restore_discarded(uid, conversation_id)
        conversations_db.update_conversation(uid, conversation_id, {DAY_REVIEW_DATE_FIELD: None})
    for conversation_id in undo.get('starred') or []:
        if conversations_db.get_conversation(uid, conversation_id):
            conversations_db.set_conversation_starred(uid, conversation_id, False)
    for conversation_id, tags in (undo.get('tags') or {}).items():
        if conversations_db.get_conversation(uid, conversation_id):
            conversations_db.set_conversation_tags(uid, conversation_id, list(tags))
    draft = dict(undo['draft'])
    day_reviews_db.set_day_review(uid, date_str, draft)
    caldav_sync.mark_dirty(uid)
    return present({**draft, 'date': date_str})


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def draft_fits(draft: Optional[Dict[str, Any]], rolled_ids: Sequence[str]) -> bool:
    """Whether a draft's text was written for exactly these rolled-up conversations (then
    approving it needs no second model call)."""
    if not is_draft(draft) or not (draft or {}).get('summary_text'):
        return False
    return set(rolled_ids) == set((draft or {}).get('rolled_up_conversation_ids') or [])


def prepare_draft(uid: str, date_str: str) -> Dict[str, Any]:
    """The hands-off split, written down for the user to review: starred conversations stay,
    every other completed one goes into the sectioned summary. A draft only: no conversation
    is starred, discarded or rolled up, and nothing reaches the calendar until ``approve``."""
    day = parse_date(date_str)
    tz_name = notification_db.get_user_time_zone(uid)
    if day > local_today(tz_name):
        raise DayReviewError(422, 'Date cannot be in the future')
    if day_reviews_db.get_day_review(uid, date_str):
        raise DayReviewError(409, 'This day already has a summary')
    loaded = load_day(uid, day, tz_name)
    if not loaded.visible:
        raise DayReviewError(400, f'No conversations to review for {date_str}')
    keep = [c for c in loaded.visible if getattr(c, 'starred', False)]
    rolled = [c for c in loaded.visible if not getattr(c, 'starred', False)]
    if rolled:
        title, body, sections = generate_summary(uid, day, tz_name, rolled, [])
    else:
        title, body, sections = 'Everything kept', '', []
    now = datetime.now(timezone.utc)
    review = {
        'status': DRAFT,
        'title': title,
        'summary_text': body,
        'sections': sections,
        'corrections': [],
        'kept_conversation_ids': [c.id for c in keep],
        'rolled_up_conversation_ids': [c.id for c in rolled],
        'merged_groups': [],
        'dropped_conversation_ids': [],
        'time_zone': tz_name,
        'created_at': now,
        'updated_at': now,
        'generated_at': now if rolled else None,
        'generation_count': 1 if rolled else 0,
    }
    day_reviews_db.set_day_review(uid, date_str, review)
    return present({**review, 'date': date_str})


def bundle(uid: str, date_str: str) -> Dict[str, Any]:
    """The hands-off review (POST /bundle, and the overnight job): a draft to approve."""
    return prepare_draft(uid, date_str)


def approve(
    uid: str,
    date_str: str,
    *,
    replace_edits: bool = False,
    approved_by: str = outbound_dispatch.APPROVED_BY_USER,
) -> Dict[str, Any]:
    """The user's OK on a draft: finalize the day with the draft's split and text. Starred
    clips stay (also ones starred after the draft was written); a conversation that came in
    since is rolled up too, and only then is the summary written again."""
    review = _require_review(uid, date_str)
    if not is_draft(review):
        raise DayReviewError(409, 'This day is already approved')
    day = parse_date(date_str)
    loaded = load_day(uid, day, notification_db.get_user_time_zone(uid))
    visible = [c.id for c in loaded.visible]  # day order
    starred = {c.id for c in loaded.visible if getattr(c, 'starred', False)}
    planned = set(review.get('kept_conversation_ids') or [])
    keep = [cid for cid in visible if cid in planned or cid in starred]
    drop = [cid for cid in (review.get('dropped_conversation_ids') or []) if cid in visible and cid not in keep]
    return finalize(uid, date_str, keep, (), drop, replace_edits=replace_edits, approved_by=approved_by)


def favorite_section(uid: str, date_str: str, section_id: str, favorited: bool) -> Dict[str, Any]:
    """Pull a section's conversations out of the bundle as starred conversations
    (or fold them back in). The section text stays in the summary either way."""
    review = _require_review(uid, date_str)
    sections = list(review.get('sections') or [])
    section = next((s for s in sections if s.get('id') == section_id), None)
    if section is None:
        raise DayReviewError(404, 'Section not found')
    if bool(section.get('favorited')) == favorited:
        return present(review)
    ids = [cid for cid in (section.get('conversation_ids') or []) if conversations_db.get_conversation(uid, cid)]
    kept = [cid for cid in (review.get('kept_conversation_ids') or []) if cid not in ids]
    rolled = [cid for cid in (review.get('rolled_up_conversation_ids') or []) if cid not in ids]
    if is_draft(review):
        # A draft only plans the split: star (or unstar) them, nothing is discarded or restored.
        for cid in ids:
            conversations_db.set_conversation_starred(uid, cid, favorited)
        (kept if favorited else rolled).extend(ids)
    elif favorited:
        for cid in ids:
            lifecycle.restore_discarded(uid, cid)
            conversations_db.set_conversation_starred(uid, cid, True)
            _submit(_enrich_held, uid, cid)
        kept.extend(ids)
        outbound_dispatch.conversations_kept(uid, ids)
    else:
        for cid in ids:
            conversations_db.set_conversation_starred(uid, cid, False)
            conversations_db.update_conversation(uid, cid, {DAY_REVIEW_DATE_FIELD: date_str})
            lifecycle.discard(uid, cid)
        rolled.extend(ids)
    section['favorited'] = favorited
    review.update(
        {
            'sections': sections,
            'kept_conversation_ids': kept,
            'rolled_up_conversation_ids': rolled,
            'updated_at': datetime.now(timezone.utc),
        }
    )
    day_reviews_db.set_day_review(uid, date_str, review)
    caldav_sync.mark_dirty(uid)
    return present(review)


def keep_conversation(uid: str, date_str: str, conversation_id: str, kept: bool) -> Dict[str, Any]:
    """Pull one clip out of the day's summary as a starred conversation (``kept``), or fold it back
    in. On a draft this only plans it (star or unstar); on an approved day the clip comes back
    (restored, starred, processed if the hold kept it raw) or goes back into the summary
    (unstarred, discarded). The summary text stays as it is either way. A section whose clips
    are all kept reads as favorited."""
    review = _require_review(uid, date_str)
    kept_ids = list(review.get('kept_conversation_ids') or [])
    rolled_ids = list(review.get('rolled_up_conversation_ids') or [])
    if conversation_id not in kept_ids and conversation_id not in rolled_ids:
        raise DayReviewError(404, 'Not a conversation of this day\'s summary')
    if (conversation_id in kept_ids) == kept:
        return present(review)
    if not conversations_db.get_conversation(uid, conversation_id):
        raise DayReviewError(404, 'Conversation not found')
    if is_draft(review):
        conversations_db.set_conversation_starred(uid, conversation_id, kept)
    elif kept:
        lifecycle.restore_discarded(uid, conversation_id)
        conversations_db.update_conversation(uid, conversation_id, {DAY_REVIEW_DATE_FIELD: None})
        conversations_db.set_conversation_starred(uid, conversation_id, True)
        _submit(_enrich_held, uid, conversation_id)
    else:
        conversations_db.set_conversation_starred(uid, conversation_id, False)
        conversations_db.update_conversation(uid, conversation_id, {DAY_REVIEW_DATE_FIELD: date_str})
        lifecycle.discard(uid, conversation_id)
    if kept:
        rolled_ids = [c for c in rolled_ids if c != conversation_id]
        kept_ids.append(conversation_id)
    else:
        kept_ids = [c for c in kept_ids if c != conversation_id]
        rolled_ids.append(conversation_id)
    sections = list(review.get('sections') or [])
    for section in sections:
        ids = list(section.get('conversation_ids') or [])
        if conversation_id in ids:
            section['favorited'] = all(c in kept_ids for c in ids)
    review.update(
        {
            'sections': sections,
            'kept_conversation_ids': kept_ids,
            'rolled_up_conversation_ids': rolled_ids,
            'updated_at': datetime.now(timezone.utc),
        }
    )
    day_reviews_db.set_day_review(uid, date_str, review)
    if not is_draft(review):
        caldav_sync.mark_dirty(uid)
        if kept:
            # "Send when I keep a clip": the wiki gets it once it is processed (outbound_dispatch).
            outbound_dispatch.conversations_kept(uid, [conversation_id])
    return present(review)


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


def regenerate(uid: str, date_str: str, *, replace_edits: bool = False) -> Dict[str, Any]:
    """Rewrite the summary with every correction as ground truth. Text the user edited by hand
    is replaced only once they confirm (``replace_edits``); otherwise 409."""
    review = _require_review(uid, date_str)
    if has_user_edits(review) and not replace_edits:
        raise DayReviewError(409, EDITS_WOULD_BE_REPLACED)
    # The summary still covers a favorited section's conversations: favoriting
    # pulls them out of the bundle, it does not remove them from the day.
    rolled_ids = list(review.get('rolled_up_conversation_ids') or [])
    for section in review.get('sections') or []:
        if section.get('favorited'):
            rolled_ids.extend(cid for cid in (section.get('conversation_ids') or []) if cid not in rolled_ids)
    if not rolled_ids:
        raise DayReviewError(400, 'Nothing was rolled up for this day, so there is no summary to rewrite')
    raw = conversations_db.get_conversations_by_id(uid, rolled_ids, include_discarded=True)
    conversations = [deserialize_conversation(c) for c in raw if c]
    if not conversations:
        raise DayReviewError(410, 'The rolled-up conversations no longer exist')
    corrections = list(review.get('corrections') or [])
    title, body, sections = generate_summary(
        uid,
        parse_date(date_str),
        review.get('time_zone') or notification_db.get_user_time_zone(uid),
        conversations,
        corrections,
        previous_summary=review.get('summary_text') or None,
    )
    # A favorited section stays favorited when its conversations land in a new section.
    favorited_ids = {
        cid for s in (review.get('sections') or []) if s.get('favorited') for cid in (s.get('conversation_ids') or [])
    }
    for row in sections:
        row['favorited'] = bool(favorited_ids) and any(cid in favorited_ids for cid in row['conversation_ids'])
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
            'sections': sections,
            'corrections': corrections,
            'updated_at': now,
            'generated_at': now,
            'generation_count': int(review.get('generation_count') or 0) + 1,
            EDITED_AT: None,
        }
    )
    review.pop(GENERATED_VERSION, None)
    day_reviews_db.set_day_review(uid, date_str, review)
    _apply_section_tags(uid, conversations, sections)
    if not is_draft(review):
        caldav_sync.mark_dirty(uid)  # an approved day's calendar summary follows the new text
    return present(review)


def _text_of(review: Dict[str, Any]) -> Dict[str, Any]:
    """The user-editable text of a review: what ``edit_text`` changes and Undo puts back."""
    return {
        'title': str(review.get('title') or ''),
        'summary_text': str(review.get('summary_text') or ''),
        'sections': [
            {'id': str(s.get('id') or ''), 'title': str(s.get('title') or ''), 'text': str(s.get('text') or '')}
            for s in review.get('sections') or []
        ],
    }


def _tidy(text: str) -> str:
    """The user's text in the stored shape: paragraphs separated by one blank line."""
    return join_paragraphs(split_paragraphs(text))


def edit_text(
    uid: str,
    date_str: str,
    *,
    title: Optional[str] = None,
    sections: Optional[Sequence[Dict[str, Any]]] = None,
    summary_text: Optional[str] = None,
) -> Dict[str, Any]:
    """The user's own words for the summary: the title, and each section's heading and text (or,
    for a summary without sections, its whole text). A draft stays a draft; an approved day stays
    approved and its summary on the calendar follows. Corrections are left as they are (a
    redaction stays enforced on the new text)."""
    review = _require_review(uid, date_str)
    before = _text_of(review)
    rows = [dict(r) for r in review.get('sections') or []]
    if sections is not None:
        by_id = {str(r.get('id') or ''): r for r in rows}
        unknown = [str(e.get('id')) for e in sections if str(e.get('id')) not in by_id]
        if unknown:
            raise DayReviewError(422, f'No such section: {", ".join(unknown[:5])}')
        for edit in sections:
            row = by_id[str(edit.get('id'))]
            if edit.get('title') is not None:
                row['title'] = str(edit['title']).strip()
            if edit.get('text') is not None:
                text = _tidy(str(edit['text']))
                if not text:
                    raise DayReviewError(422, 'A section cannot be empty')
                row['text'] = text
        review['sections'] = rows
        review['summary_text'] = join_paragraphs(str(r.get('text') or '') for r in rows)
    if summary_text is not None:
        if rows:
            raise DayReviewError(422, 'This summary has sections: edit the sections')
        text = _tidy(summary_text)
        if not text:
            raise DayReviewError(422, 'The summary cannot be empty')
        review['summary_text'] = text
    if title is not None:
        if not title.strip():
            raise DayReviewError(422, 'The title cannot be empty')
        review['title'] = title.strip()
    after = _text_of(review)
    if after == before:
        return present(review)
    now = datetime.now(timezone.utc)
    if not has_user_edits(review):
        review[GENERATED_VERSION] = before
    if after == review.get(GENERATED_VERSION):
        # Put back as the model wrote it (Undo): nothing of the user's would be lost to a rewrite.
        review[EDITED_AT] = None
        review.pop(GENERATED_VERSION, None)
    else:
        review[EDITED_AT] = now
    review['updated_at'] = now
    day_reviews_db.set_day_review(uid, date_str, review)
    if not is_draft(review):
        caldav_sync.mark_dirty(uid)
    return present(review)


def reopen(uid: str, date_str: str) -> Tuple[int, int]:
    """Undo an approval: restore the rolled-up and dropped conversations and turn the review
    back into a draft (its summary comes off the calendar until it is approved again; the
    user can approve it as it is or sort the day anew). Reopening a draft discards it.
    Returns (restored, merged groups that stay merged)."""
    review = _require_review(uid, date_str)
    if is_draft(review):
        day_reviews_db.delete_day_review(uid, date_str)
        return 0, 0
    restored = 0
    hidden = [*(review.get('rolled_up_conversation_ids') or []), *(review.get('dropped_conversation_ids') or [])]
    for conversation_id in hidden:
        if not conversations_db.get_conversation(uid, conversation_id):
            continue
        lifecycle.restore_discarded(uid, conversation_id)
        conversations_db.update_conversation(uid, conversation_id, {DAY_REVIEW_DATE_FIELD: None})
        restored += 1
    if review.get('summary_text') or review.get('corrections'):
        draft = {**review, 'status': DRAFT, 'merged_groups': [], 'updated_at': datetime.now(timezone.utc)}
        draft.pop('approved_at', None)
        draft.pop(UNDO_APPROVAL, None)
        day_reviews_db.set_day_review(uid, date_str, draft)
    else:
        day_reviews_db.delete_day_review(uid, date_str)
    caldav_sync.mark_dirty(uid)
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
    reviews = day_reviews_db.list_day_reviews(uid, limit=max(days, 1) + 7)
    reviewed = {r.get('date') for r in reviews if not is_draft(r)}
    drafts = {str(r.get('date')) for r in reviews if is_draft(r)}
    first_str = first.isoformat()
    return {
        'today': today.isoformat(),
        'pending_dates': sorted(d for d in dates | drafts if d not in reviewed and d >= first_str),
        'draft_dates': sorted(d for d in drafts if d >= first_str),
    }


def list_reviews(uid: str, limit: int = 30, *, include_drafts: bool = True) -> List[Dict[str, Any]]:
    reviews = day_reviews_db.list_day_reviews(uid, limit=limit)
    return [present(r) for r in reviews if include_drafts or not is_draft(r)]
