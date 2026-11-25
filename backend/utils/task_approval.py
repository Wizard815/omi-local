"""Tasks wait for the user's OK (omi-local).

A task the pipeline extracted from a conversation starts as ``approval = 'pending'``:
it is stored (the extraction still writes, INV-TASK-2), but it is not pushed to CalDAV,
not delivered to a task integration (ALLGlance/Vikunja, Todoist...) and fires no
reminder until the user approves it in the app (Tasks tab, "Waiting for your OK", or the
day review). Declining keeps the row as ``'declined'`` so it can be undone; a declined
task stays out of every list and every sync.

Everything else counts as approved: tasks the user makes in the app, in chat or over
MCP, tasks imported from a phone's CalDAV list, and every task that existed before this
field (no ``approval`` field = approved), so nothing already on a phone is pulled off.

OMI_TASK_APPROVAL=off is the operator's switch back to tasks being approved on creation.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

import database.action_items as action_items_db
from utils.notification_dispatch import dispatch_action_item_reminder
from utils.task_sync import auto_sync_action_items_batch

logger = logging.getLogger(__name__)

PENDING = 'pending'
APPROVED = 'approved'
DECLINED = 'declined'
FIELD = 'approval'
# A waiting due-date change to an approved task (utils/conversations/task_merge.py).
UPDATES_FIELD = 'updates_task_id'
WAITING_LIMIT = 500


class TaskApprovalError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def approval_enabled() -> bool:
    return os.getenv('OMI_TASK_APPROVAL', '').strip().lower() not in {'0', 'off', 'false', 'no'}


def state(item: Optional[Mapping[str, Any]]) -> str:
    return str((item or {}).get(FIELD) or APPROVED)


def is_waiting(item: Optional[Mapping[str, Any]]) -> bool:
    return state(item) == PENDING


def is_approved(item: Optional[Mapping[str, Any]]) -> bool:
    return state(item) == APPROVED


def mark_extracted(
    items: Sequence[Dict[str, Any]],
    document_ids: Optional[Sequence[Optional[str]]] = None,
    prior_rows: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Extraction output about to be written: new tasks wait for approval; a task that keeps
    an earlier row's id (re-extraction) keeps that row's answer, so an approved task is not
    asked about again and a declined one does not come back."""
    if not approval_enabled():
        return
    prior = {str(r.get('id')): r for r in prior_rows if r.get('id')}
    for index, item in enumerate(items):
        kept = document_ids[index] if document_ids is not None and index < len(document_ids) else None
        if kept and kept in prior:
            item[FIELD] = state(prior[kept])
        else:
            item[FIELD] = PENDING


def deliverable(item: Mapping[str, Any]) -> bool:
    """Whether a just-written task may reach reminders and task integrations now."""
    return is_approved(item)


# ---- the user's answer ------------------------------------------------------------------------


def _load(uid: str, task_id: str) -> Dict[str, Any]:
    item = action_items_db.get_action_item(uid, task_id)
    if not item or item.get('deleted'):
        raise TaskApprovalError(404, 'Task not found')
    return item


def _deliver(uid: str, item: Dict[str, Any]) -> None:
    """What the extraction held back: the reminder and the task integration's copy."""
    due_at = item.get('due_at')
    if due_at and not item.get('completed'):
        try:
            dispatch_action_item_reminder(
                user_id=uid,
                action_item_id=item['id'],
                description=item.get('description', ''),
                due_at=due_at.isoformat() if isinstance(due_at, datetime) else str(due_at),
                completed=False,
                status=item.get('status'),
                deleted=False,
            )
        except Exception as error:  # noqa: BLE001 — the approval stands; the reminder is a bonus
            logger.warning('task_approval: reminder failed uid=%s %s', uid, type(error).__name__)
    try:
        asyncio.run(auto_sync_action_items_batch(uid, [item]))
    except Exception as error:  # noqa: BLE001 — the integration retries on its own schedule
        logger.warning('task_approval: integration delivery failed uid=%s %s', uid, type(error).__name__)


def approve(
    uid: str,
    task_id: str,
    *,
    description: Optional[str] = None,
    due_at: Optional[datetime] = None,
    deliver: bool = True,
) -> Dict[str, Any]:
    """The user's yes, with their edits. Idempotent; a declined task can be approved too.

    A waiting update (``updates_task_id``) is applied to the approved task it updates, and
    the update row goes; the approved task is returned."""
    item = _load(uid, task_id)
    if item.get(UPDATES_FIELD):
        applied = _apply_update(uid, {**item, 'id': task_id}, due_at=due_at)
        if applied is not None:
            return applied
    update: Dict[str, Any] = {FIELD: APPROVED}
    if description is not None and description.strip():
        update['description'] = description.strip()
    if due_at is not None:
        update['due_at'] = due_at
    was_approved = is_approved(item)
    action_items_db.update_action_item(uid, task_id, update)
    current = action_items_db.get_action_item(uid, task_id) or {**item, **update}
    current['id'] = task_id
    if deliver and not was_approved:
        # Inside the call, not a detached submit: a restart right after the user's yes would
        # otherwise lose the reminder and the task app's copy, with nothing to redo them (the
        # task is approved by then, so it is never delivered again).
        _deliver(uid, current)
    return current


def _apply_update(uid: str, update_row: Dict[str, Any], *, due_at: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """Move an approved update's due date onto its task and remove the update row. None when
    that task is gone or done (the update is then approved as a task of its own)."""
    target_id = str(update_row.get(UPDATES_FIELD) or '')
    target = action_items_db.get_action_item(uid, target_id) if target_id else None
    if not target or target.get('deleted') or target.get('completed'):
        return None
    new_due = due_at if due_at is not None else update_row.get('due_at')
    if new_due is not None:
        action_items_db.update_action_item(uid, target_id, {'due_at': new_due})
    action_items_db.delete_action_item(uid, str(update_row['id']))
    current = action_items_db.get_action_item(uid, target_id) or {**target, 'due_at': new_due}
    current['id'] = target_id
    if new_due is not None and is_approved(current):
        try:
            dispatch_action_item_reminder(
                user_id=uid,
                action_item_id=target_id,
                description=current.get('description', ''),
                due_at=new_due.isoformat() if isinstance(new_due, datetime) else str(new_due),
                completed=False,
                status=current.get('status'),
                deleted=False,
            )
        except Exception as error:  # noqa: BLE001 — the new date stands; the reminder is a bonus
            logger.warning('task_approval: reminder reschedule failed uid=%s %s', uid, type(error).__name__)
    return current


def decline(uid: str, task_id: str, declined: bool = True) -> Dict[str, Any]:
    """The user's no (kept, so it can be undone), or (``declined=False``) the undo: the task
    waits again. Only a waiting or declined task can be declined; an approved task is
    deleted or completed instead."""
    item = _load(uid, task_id)
    current = state(item)
    if declined and current == APPROVED:
        raise TaskApprovalError(409, 'This task is already approved; delete it instead')
    target = DECLINED if declined else PENDING
    if current != target:
        action_items_db.update_action_item(uid, task_id, {FIELD: target})
    return {**item, FIELD: target, 'id': task_id}


def approve_many(uid: str, task_ids: Sequence[str]) -> List[str]:
    """Approve all: the user's yes to each of these waiting tasks, as if tapped one by one.
    Returns the ids it approved (one that is gone, or no longer waiting, is skipped)."""
    done: List[str] = []
    for task_id in dict.fromkeys(task_ids):
        try:
            if not is_waiting(_load(uid, task_id)):
                continue
            approve(uid, task_id)
        except TaskApprovalError:
            continue
        done.append(task_id)
    return done


def take_back(uid: str, task_id: str) -> bool:
    """Undo an approval (Approve all's Undo): the task waits again, so the CalDAV sync takes it off
    the user's task list, and its reminder is called off. False when there is nothing to undo
    (gone, or not approved). A task app's copy made at approval (Todoist, ALLGlance...) stays:
    those integrations have no recall."""
    try:
        item = _load(uid, task_id)
    except TaskApprovalError:
        return False
    if not is_approved(item):
        return False
    action_items_db.update_action_item(uid, task_id, {FIELD: PENDING})
    due_at = item.get('due_at')
    if due_at and not item.get('completed'):
        try:
            dispatch_action_item_reminder(
                user_id=uid,
                action_item_id=task_id,
                description=item.get('description', ''),
                due_at=due_at.isoformat() if isinstance(due_at, datetime) else str(due_at),
                completed=False,
                status=item.get('status'),
                deleted=True,
            )
        except Exception as error:  # noqa: BLE001 — the undo stands; the phone's reminder is a bonus
            logger.warning('task_approval: reminder recall failed uid=%s %s', uid, type(error).__name__)
    return True


def waiting(uid: str, *, limit: int = WAITING_LIMIT) -> List[Dict[str, Any]]:
    """The open tasks waiting for the user's OK, oldest first."""
    items = action_items_db.get_action_items(uid, completed=False, limit=limit)
    rows = [dict(i) for i in items if is_waiting(i) and not i.get('deleted')]
    rows.sort(key=lambda r: str(r.get('created_at') or ''))
    return rows


def approve_waiting_before(uid: str, before: datetime) -> List[str]:
    """Auto-approve: every waiting task created before ``before`` (a past day), as if the user
    tapped Approve on each. Returns the ids approved."""
    done: List[str] = []
    for item in waiting(uid):
        created = item.get('created_at')
        if not isinstance(created, datetime) or created >= before:
            continue
        try:
            approve(uid, str(item['id']))
        except TaskApprovalError:
            continue
        done.append(str(item['id']))
    return done
