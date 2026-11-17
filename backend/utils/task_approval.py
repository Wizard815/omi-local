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

logger = logging.getLogger(__name__)

PENDING = 'pending'
APPROVED = 'approved'
DECLINED = 'declined'
FIELD = 'approval'
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
    from utils.notifications import send_action_item_data_message
    from utils.task_sync import auto_sync_action_items_batch

    due_at = item.get('due_at')
    if due_at and not item.get('completed'):
        try:
            send_action_item_data_message(
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
    """The user's yes, with their edits. Idempotent; a declined task can be approved too."""
    item = _load(uid, task_id)
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
        from utils.executors import postprocess_executor, submit_with_context

        submit_with_context(postprocess_executor, _deliver, uid, current)
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
