"""Deterministic merge of re-extracted tasks into the user's open tasks (omi-local).

The notes prompt sees the user's similar open tasks, but a model still re-extracts them:
"Venmo Gary for the plumbing work and parts" on Monday comes back on Tuesday as "Venmo
Gary for the completed plumbing work". This pass runs after extraction, before the
write, and turns such an item into no new task.

A new item merges into an open task (not completed, deleted or declined) from another
conversation, last touched within ``OMI_TASK_MERGE_DAYS`` (14), when

- their normalized text matches (case, punctuation, articles), or
- their task vectors score at least ``OMI_TASK_MERGE_THRESHOLD`` (0.80).

The threshold comes from measured task-to-task scores (text-embedding-3-small, cosine):
real duplicates 0.80-0.95, different tasks 0.10-0.71 except two tasks that share every
word but the object (flights / hotel to the same conference, 0.85). A whole transcript
against a task scores only 0.45-0.54, so the prompt's 0.45 cut is no guide here.

Approval is never bypassed (utils/task_approval.py):

- the open task waits for the user's OK: the item is dropped; a due date the later
  conversation gave is moved onto the waiting task, which the user approves anyway;
- the open task is approved and the item adds nothing: the item is dropped;
- the open task is approved and the item changes its due date: the item is written as
  a waiting task carrying ``updates_task_id``. Approving it moves the date onto the
  approved task and removes the update; declining leaves the approved task alone.

Every merge is recorded on the open task (``merged_from``: conversation, text, score).
A vector store failure merges nothing (the write goes ahead as before).
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import database.action_items as action_items_db
import database.conversations as conversations_db
from database.vector_db import find_similar_action_items
from utils import task_approval
from utils.observability.fallback import record_fallback

logger = logging.getLogger(__name__)

MERGE_ENV = 'OMI_TASK_MERGE'
THRESHOLD_ENV = 'OMI_TASK_MERGE_THRESHOLD'
DAYS_ENV = 'OMI_TASK_MERGE_DAYS'
DEFAULT_THRESHOLD = 0.80
DEFAULT_DAYS = 14
CANDIDATES_PER_ITEM = 5
UPDATES_FIELD = task_approval.UPDATES_FIELD
MERGED_FIELD = 'merged_from'
MERGED_KEEP = 20

_ARTICLES = {'a', 'an', 'the', 'to', 'for', 'my', 'our', 'his', 'her', 'their'}


def merge_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(MERGE_ENV, '').strip().lower() not in {'0', 'off', 'false', 'no'}


def merge_threshold(environ: Optional[Mapping[str, str]] = None) -> float:
    env = os.environ if environ is None else environ
    try:
        value = float(env.get(THRESHOLD_ENV, '').strip() or DEFAULT_THRESHOLD)
    except ValueError:
        value = DEFAULT_THRESHOLD
    return min(1.0, max(0.0, value))


def merge_days(environ: Optional[Mapping[str, str]] = None) -> int:
    env = os.environ if environ is None else environ
    try:
        value = int(env.get(DAYS_ENV, '').strip() or DEFAULT_DAYS)
    except ValueError:
        value = DEFAULT_DAYS
    return max(1, value)


def normalize(text: Optional[str]) -> str:
    """Lowercase, accents and punctuation off, articles and possessives dropped."""
    folded = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    folded = folded.replace("'s ", ' ').replace('[screen]', ' ')
    words = [w for w in re.findall(r'[a-z0-9$]+', folded) if w not in _ARTICLES]
    return ' '.join(words)


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _same_due(a: Any, b: Any) -> bool:
    left, right = _as_utc(a), _as_utc(b)
    if left is None or right is None:
        return left is None and right is None
    return abs((left - right).total_seconds()) < 60


@dataclass
class MergeOutcome:
    kept: List[Dict[str, Any]] = field(default_factory=list)
    merged: List[Dict[str, Any]] = field(default_factory=list)  # {'item', 'into', 'score', 'action'}


def _excluded_conversations(conversation: Any) -> set:
    excluded = {getattr(conversation, 'id', None)}
    external = getattr(conversation, 'external_data', None) or {}
    excluded.update((external.get('merge_metadata') or {}).get('source_conversation_ids') or [])
    excluded.discard(None)
    return excluded


def _open_candidate(row: Mapping[str, Any], excluded: set, cutoff: datetime) -> bool:
    if row.get('completed') or row.get('deleted'):
        return False
    if task_approval.state(row) == task_approval.DECLINED or row.get(UPDATES_FIELD):
        return False
    if row.get('conversation_id') in excluded:
        return False
    last = _as_utc(row.get('updated_at')) or _as_utc(row.get('created_at'))
    return last is not None and last >= cutoff


def _conversation_started(uid: str, conversation_id: Optional[str]) -> Optional[datetime]:
    if not conversation_id:
        return None
    try:
        row = conversations_db.get_conversation(uid, conversation_id) or {}
    except Exception:  # noqa: BLE001 — an unreadable source conversation means "not later"
        return None
    return _as_utc(row.get('started_at')) or _as_utc(row.get('created_at'))


def _later_than_source(uid: str, conversation: Any, existing: Mapping[str, Any]) -> bool:
    """Only a conversation newer than the open task's own moves its due date."""
    mine = _as_utc(getattr(conversation, 'started_at', None)) or _as_utc(getattr(conversation, 'created_at', None))
    theirs = _conversation_started(uid, existing.get('conversation_id'))
    if mine is None:
        return False
    return theirs is None or mine >= theirs


def find_match(
    uid: str,
    item: Mapping[str, Any],
    *,
    excluded: set,
    cutoff: datetime,
    threshold: float,
    similar: Callable[..., List[Dict[str, Any]]] = find_similar_action_items,
) -> Optional[tuple]:
    """The open task this item repeats, as ``(row, score)``, or None."""
    description = str(item.get('description') or '').strip()
    if not description:
        return None
    hits = similar(uid, description, threshold=0.0, limit=CANDIDATES_PER_ITEM)
    if not hits:
        return None
    scores = {h['action_item_id']: float(h.get('score') or 0.0) for h in hits}
    rows = action_items_db.get_action_items_by_ids(uid, list(scores))
    target = normalize(description)
    best: Optional[tuple] = None
    for row in rows:
        if not _open_candidate(row, excluded, cutoff):
            continue
        score = scores.get(str(row.get('id')), 0.0)
        if normalize(row.get('description')) == target:
            score = max(score, 1.0)
        if score < threshold:
            continue
        if best is None or score > best[1]:
            best = (row, score)
    return best


def _record(uid: str, existing: Mapping[str, Any], conversation_id: str, item: Mapping[str, Any], score: float, update):
    trail = list(existing.get(MERGED_FIELD) or [])
    trail.append(
        {
            'conversation_id': conversation_id,
            'description': str(item.get('description') or ''),
            'score': round(score, 3),
        }
    )
    action_items_db.update_action_item(uid, str(existing['id']), {**update, MERGED_FIELD: trail[-MERGED_KEEP:]})


def merge_into_open_tasks(
    uid: str,
    conversation: Any,
    items: Sequence[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    environ: Optional[Mapping[str, str]] = None,
    similar: Callable[..., List[Dict[str, Any]]] = find_similar_action_items,
) -> MergeOutcome:
    """Split the extracted items into the ones to write and the ones merged away."""
    outcome = MergeOutcome()
    if not items or not merge_enabled(environ):
        outcome.kept = list(items)
        return outcome

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=merge_days(environ))
    threshold = merge_threshold(environ)
    excluded = _excluded_conversations(conversation)
    conversation_id = str(getattr(conversation, 'id', '') or '')
    approval_on = task_approval.approval_enabled()
    own_rows: Optional[List[Dict[str, Any]]] = None

    for item in items:
        try:
            match = find_match(uid, item, excluded=excluded, cutoff=cutoff, threshold=threshold, similar=similar)
        except Exception as error:  # noqa: BLE001 — no merge is the old behaviour, never a lost task
            logger.warning('task_merge: lookup failed uid=%s %s', uid, type(error).__name__)
            record_fallback(
                component='conversation_notes',
                from_mode='task_merge',
                to_mode='write_unmerged',
                reason='unavailable',
                outcome='degraded',
            )
            match = None
        if match is None:
            outcome.kept.append(item)
            continue
        existing, score = match
        if own_rows is None:
            own_rows = action_items_db.get_action_items_by_conversation(uid, conversation_id) if conversation_id else []
        if any(normalize(r.get('description')) == normalize(item.get('description')) for r in own_rows):
            # This conversation already wrote it (before merging existed): replacement keeps
            # that row and its answer; dropping it here would delete an approved task.
            outcome.kept.append(item)
            continue

        new_due = item.get('due_at')
        due_changes = (
            new_due is not None
            and not _same_due(new_due, existing.get('due_at'))
            and _later_than_source(uid, conversation, existing)
        )
        if not due_changes:
            _record(uid, existing, conversation_id, item, score, {})
            outcome.merged.append({'item': item, 'into': existing['id'], 'score': score, 'action': 'dropped'})
        elif task_approval.is_waiting(existing) or not approval_on:
            # Still waiting (the user approves the new date with it) or approvals are off.
            _record(uid, existing, conversation_id, item, score, {'due_at': new_due})
            outcome.merged.append({'item': item, 'into': existing['id'], 'score': score, 'action': 'due_moved'})
        else:
            # Approved: the new date asks for the user's OK on its own.
            update = dict(item)
            update[UPDATES_FIELD] = str(existing['id'])
            update['description'] = str(existing.get('description') or item.get('description') or '')
            outcome.kept.append(update)
            outcome.merged.append({'item': item, 'into': existing['id'], 'score': score, 'action': 'update_waits'})

    for merge in outcome.merged:
        logger.info(
            'task_merge uid=%s conversation=%s into=%s score=%.3f action=%s',
            uid,
            conversation_id,
            merge['into'],
            merge['score'],
            merge['action'],
        )
    return outcome
