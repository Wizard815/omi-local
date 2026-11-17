"""What waits for the user's OK (omi-local): the tasks Omi extracted from conversations,
and the auto-approve setting. Events are approved in the Calendar check
(routers/family_calendar.py) and day summaries in the day review (routers/day_reviews.py);
GET /v1/approvals counts all three for the app's banner. See utils/task_approval.py and
utils/auto_approve.py."""

from fastapi import APIRouter, Depends, HTTPException

import database.approval_settings as approval_db
from models.action_item import ActionItemResponse
from models.approvals import (
    ApprovalSettings,
    ApproveTaskRequest,
    DeclineTaskRequest,
    TaskApprovalResponse,
    UpdateApprovalSettingsRequest,
    WaitingForApprovalResponse,
)
from utils import auto_approve, task_approval
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter()


@router.get('/v1/approvals', tags=['approvals'], response_model=WaitingForApprovalResponse)
async def get_waiting_for_approval(uid: str = Depends(auth.get_current_user_uid)):
    """Tasks waiting for approval, and how many heard events and day summary drafts wait too."""
    return await run_blocking(db_executor, auto_approve.waiting_summary, uid)


@router.post('/v1/approvals/tasks/{task_id}/approve', tags=['approvals'], response_model=TaskApprovalResponse)
async def approve_task(task_id: str, request: ApproveTaskRequest, uid: str = Depends(auth.get_current_user_uid)):
    """Approve one task, with the user's edits: it reaches their CalDAV list, reminders and task apps."""
    try:
        task = await run_blocking(
            db_executor,
            lambda: task_approval.approve(uid, task_id, description=request.description, due_at=request.due_at),
        )
    except task_approval.TaskApprovalError as error:
        if error.status_code == 404:
            raise HTTPException(status_code=404, detail=error.message)
        return TaskApprovalResponse(ok=False, error=error.message)
    return TaskApprovalResponse(ok=True, task=ActionItemResponse.model_validate(task))


@router.post('/v1/approvals/tasks/{task_id}/decline', tags=['approvals'], response_model=TaskApprovalResponse)
async def decline_task(task_id: str, request: DeclineTaskRequest, uid: str = Depends(auth.get_current_user_uid)):
    """Decline one waiting task (kept, never synced), or (``declined: false``) undo that: it waits again."""
    try:
        task = await run_blocking(db_executor, task_approval.decline, uid, task_id, request.declined)
    except task_approval.TaskApprovalError as error:
        if error.status_code == 404:
            raise HTTPException(status_code=404, detail=error.message)
        return TaskApprovalResponse(ok=False, error=error.message)
    return TaskApprovalResponse(ok=True, task=ActionItemResponse.model_validate(task))


def _present(settings: dict) -> ApprovalSettings:
    return ApprovalSettings(
        auto_approve=bool(settings.get('auto_approve')),
        time=str(settings.get('time') or '21:00'),
        operator_disabled=not auto_approve.operator_allows(),
    )


@router.get('/v1/approvals/settings', tags=['approvals'], response_model=ApprovalSettings)
async def get_approval_settings(uid: str = Depends(auth.get_current_user_uid)):
    return _present(await run_blocking(db_executor, approval_db.get_settings, uid))


@router.put('/v1/approvals/settings', tags=['approvals'], response_model=ApprovalSettings)
async def update_approval_settings(
    request: UpdateApprovalSettingsRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Turn auto-approve on or off and set its local time (off by default)."""
    data = request.model_dump(exclude_none=True)
    return _present(await run_blocking(db_executor, approval_db.set_settings, uid, data))
