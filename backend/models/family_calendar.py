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


class FamilyCalendarResponse(BaseModel):
    configured: bool = Field(description='A family calendar is reachable (the ALLGlance gateway with dayGLANCE)')
    label: str = Field(default='', description='What to call it in the app')
    pending: List[FamilyCalendarEvent] = Field(default_factory=list, description='Heard, not yet reviewed')
    recent: List[FamilyCalendarEvent] = Field(default_factory=list, description='Reviewed lately (added or declined)')


class ApproveFamilyCalendarEventRequest(BaseModel):
    conversation_id: str
    event_idx: int = Field(ge=0)
    title: Optional[str] = Field(default=None, max_length=200, description='Edited title; omitted keeps Omi\'s')
    description: Optional[str] = Field(default=None, max_length=2000)
    start: Optional[datetime] = Field(default=None, description='Edited start (with offset); omitted keeps Omi\'s')
    duration: Optional[int] = Field(default=None, ge=5, le=24 * 60, description='Edited length in minutes')


class DeclineFamilyCalendarEventRequest(BaseModel):
    conversation_id: str
    event_idx: int = Field(ge=0)


class FamilyCalendarEventResponse(BaseModel):
    ok: bool
    event: Optional[FamilyCalendarEvent] = None
    error: str = ''
