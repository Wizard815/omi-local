"""API shapes for the family calendar review (routers/family_calendar.py).

Omi hears events in conversations; none of them reaches the family calendar (dayGLANCE,
through the ALLGlance gateway) until the user has looked at the date and time, fixed what
Omi got wrong, and approved it. These are the shapes of that review."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class FamilyCalendarEvent(BaseModel):
    conversation_id: str
    event_idx: int = Field(description='Index in the conversation\'s structured events')
    conversation_title: str = ''
    title: str
    description: str = ''
    start: datetime = Field(description='UTC; the app shows it in the user\'s time zone')
    duration: int = Field(default=30, description='Minutes')
    heard_at: Optional[datetime] = Field(default=None, description='When the conversation happened')
    created: bool = False
    calendar: Optional[str] = None
    external_id: Optional[str] = None
    declined: bool = False
    approved: bool = Field(default=False, description='The user said yes; on their CalDAV calendar once synced')


class FamilyCalendarMember(BaseModel):
    id: str
    name: str


class FamilyCalendarResponse(BaseModel):
    configured: bool = Field(description='A family calendar is reachable (the ALLGlance gateway with dayGLANCE)')
    caldav: bool = Field(
        default=False, description='Approved events also go to the user\'s CalDAV calendar (Settings, Calendar sync)'
    )
    label: str = Field(default='', description='What to call it in the app')
    members: List[FamilyCalendarMember] = Field(
        default_factory=list, description='The household (the gateway\'s roster); empty when it could not be read'
    )
    owner_id: Optional[str] = Field(default=None, description='The member Omi adds events as; None = the whole family')
    owner_name: str = ''
    pending: List[FamilyCalendarEvent] = Field(default_factory=list, description='Heard, not yet reviewed')
    recent: List[FamilyCalendarEvent] = Field(default_factory=list, description='Reviewed lately (added or declined)')


class ApproveFamilyCalendarEventRequest(BaseModel):
    conversation_id: str
    event_idx: int = Field(ge=0)
    title: Optional[str] = Field(default=None, max_length=200, description='Edited title; omitted keeps Omi\'s')
    description: Optional[str] = Field(default=None, max_length=2000)
    start: Optional[datetime] = Field(default=None, description='Edited start (with offset); omitted keeps Omi\'s')
    duration: Optional[int] = Field(default=None, ge=5, le=24 * 60, description='Edited length in minutes')


class SetFamilyCalendarOwnerRequest(BaseModel):
    owner_id: Optional[str] = Field(
        default=None, max_length=128, description='A member id from the roster; null = whole family'
    )


class DeclineFamilyCalendarEventRequest(BaseModel):
    conversation_id: str
    event_idx: int = Field(ge=0)
    declined: bool = Field(default=True, description='False takes a decline back: the event waits for review again')


class FamilyCalendarEventResponse(BaseModel):
    ok: bool
    event: Optional[FamilyCalendarEvent] = None
    error: str = ''


class FamilyDayBlock(BaseModel):
    title: str
    start: Optional[datetime] = Field(default=None, description='UTC; None for an all-day block')
    time_label: str = Field(default='', description='"14:30" in the user\'s time zone, or "All day"')
    duration_minutes: Optional[int] = None
    all_day: bool = False
    owner_id: Optional[str] = None


class FamilyDayResponse(BaseModel):
    """Today on the family calendar, small enough for a home-screen widget."""

    configured: bool = Field(description='A family calendar (the ALLGlance gateway) is set up for this user')
    reachable: bool = Field(default=False, description='The gateway answered; False keeps the widget\'s last data')
    date: str = Field(default='', description='Today in the user\'s time zone, YYYY-MM-DD')
    owner_name: str = Field(default='', description='The member it is for; empty = the whole family')
    next: List[FamilyDayBlock] = Field(
        default_factory=list, description='What is still ahead today, soonest first (all-day blocks first)'
    )
    remaining: int = Field(default=0, description='Blocks still ahead today, including those not in next')
    tasks_due: int = Field(default=0, description='Open tasks due today, from every source')
    chores_overdue: int = 0
    chores: List[str] = Field(default_factory=list, description='The overdue chores\' names, at most three')
    error: str = Field(default='', description='What went wrong, in words the app can show')
