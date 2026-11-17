"""API shapes for what waits for the user's OK (routers/approvals.py, omi-local): tasks
extracted from conversations, heard calendar events and day summary drafts, and the
user's auto-approve setting."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from models.action_item import ActionItemResponse


class ApprovalSettings(BaseModel):
    auto_approve: bool = Field(
        default=False,
        description="Approve what still waits from past days at `time`, as if the user tapped Approve. Off by default",
    )
    time: str = Field(default='21:00', description="The user's local time, HH:MM")
    operator_disabled: bool = Field(default=False, description='The server operator turned auto-approve off')


class UpdateApprovalSettingsRequest(BaseModel):
    auto_approve: Optional[bool] = None
    time: Optional[str] = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$', description='HH:MM, local')


class WaitingForApprovalResponse(BaseModel):
    tasks: List[ActionItemResponse] = Field(
        default_factory=list, description='Tasks waiting for approval, oldest first'
    )
    task_count: int = 0
    event_count: int = Field(default=0, description='Heard events waiting in the Calendar check')
    draft_dates: List[str] = Field(default_factory=list, description='Days with a summary draft to approve')
    today: str = ''
    auto_approve: bool = False
    auto_approve_time: str = ''


class ApproveTaskRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=2000, description='Edited text; omitted keeps it')
    due_at: Optional[datetime] = Field(default=None, description='Edited due date; omitted keeps it')


class DeclineTaskRequest(BaseModel):
    declined: bool = Field(default=True, description='False takes a decline back: the task waits again')


class TaskApprovalResponse(BaseModel):
    ok: bool
    task: Optional[ActionItemResponse] = None
    error: str = ''
