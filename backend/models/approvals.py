"""API shapes for what waits for the user's OK (routers/approvals.py, omi-local): tasks
extracted from conversations, heard calendar events and day summary drafts, and the
user's auto-approve setting."""

from datetime import datetime
from typing import Annotated, List, Optional

from pydantic import BaseModel, Field

from models.action_item import ActionItemResponse
from models.family_calendar import FamilyCalendarEvent


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
    events: List[FamilyCalendarEvent] = Field(
        default_factory=list, description='Those events, soonest first (for choosing what to approve)'
    )
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


class ApprovalEventRef(BaseModel):
    conversation_id: str = Field(max_length=128)
    event_idx: int = Field(ge=0, le=1000)


_DATE_PATTERN = r'^\d{4}-\d{2}-\d{2}$'
_ID = Annotated[str, Field(min_length=1, max_length=128)]
_DATE = Annotated[str, Field(pattern=_DATE_PATTERN)]


class ApproveBatchRequest(BaseModel):
    all_waiting: bool = Field(
        default=False,
        description='Approve everything waiting (tasks, events, day summary drafts); the lists are ignored',
    )
    date: Optional[str] = Field(
        default=None,
        pattern=_DATE_PATTERN,
        description="With all_waiting: only what is of this local day (the day review's \"From this day\")",
    )
    task_ids: List[_ID] = Field(default_factory=list, max_length=500)
    events: List[ApprovalEventRef] = Field(default_factory=list, max_length=500)
    day_dates: List[_DATE] = Field(
        default_factory=list, max_length=31, description='Days whose summary draft to approve'
    )


class ApproveBatchResponse(BaseModel):
    task_ids: List[str] = Field(default_factory=list, description='The tasks approved: what Undo takes back')
    events: List[ApprovalEventRef] = Field(default_factory=list, description='The events approved')
    day_dates: List[str] = Field(default_factory=list, description='The days whose summary draft was approved')
    task_count: int = 0
    event_count: int = 0
    day_count: int = 0
    failed_event_count: int = Field(default=0, description='Events the family calendar refused; they still wait')
    failed_day_dates: List[str] = Field(
        default_factory=list, description='Days that could not be approved now (they still wait in their day review)'
    )


class UndoApproveBatchRequest(BaseModel):
    task_ids: List[_ID] = Field(default_factory=list, max_length=500)
    events: List[ApprovalEventRef] = Field(default_factory=list, max_length=500)
    day_dates: List[_DATE] = Field(default_factory=list, max_length=31)


class UndoApproveBatchResponse(BaseModel):
    task_count: int = Field(default=0, description='Tasks waiting again')
    event_count: int = Field(default=0, description='Events waiting again')
    day_count: int = Field(default=0, description='Days back to their draft, as it was')
    kept_count: int = Field(default=0, description='Items that could not be taken back here')
