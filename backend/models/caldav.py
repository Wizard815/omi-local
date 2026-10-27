"""API shapes for the CalDAV integration (routers/caldav.py)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CalDavSettingsResponse(BaseModel):
    configured: bool = Field(description='A server is set (the user\'s own or the bundled one)')
    bundled: bool = Field(default=False, description='Using omi-host\'s own CalDAV server (no settings stored)')
    url: str = ''
    username: str = ''
    has_password: bool = False
    calendar_href: str = ''
    tasks_href: str = ''
    enabled: bool = True
    push_tasks: bool = True
    pull_tasks: bool = True
    push_day_summaries: bool = True
    push_conversation_events: bool = True
    push_conversations: bool = False
    pull_events: bool = True
    last_sync_at: Optional[datetime] = None
    last_error: str = ''
    last_report: Dict[str, Any] = Field(default_factory=dict)


class UpdateCalDavSettingsRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2048, description='Server or calendar-home URL')
    username: Optional[str] = Field(default=None, max_length=256)
    password: Optional[str] = Field(default=None, max_length=1024, description='Omitted = keep the stored one')
    calendar_href: Optional[str] = Field(default=None, max_length=2048, description='Events calendar (empty = auto)')
    tasks_href: Optional[str] = Field(default=None, max_length=2048, description='Tasks calendar (empty = auto)')
    enabled: Optional[bool] = None
    push_tasks: Optional[bool] = None
    pull_tasks: Optional[bool] = None
    push_day_summaries: Optional[bool] = None
    push_conversation_events: Optional[bool] = None
    push_conversations: Optional[bool] = None
    pull_events: Optional[bool] = None


class CalDavCalendar(BaseModel):
    href: str
    name: str = ''
    components: List[str] = Field(default_factory=list)


class CalDavTestResponse(BaseModel):
    ok: bool
    calendars: List[CalDavCalendar] = Field(default_factory=list)
    error: str = ''


class CalDavSyncResponse(BaseModel):
    ok: bool
    tasks_pushed: int = 0
    tasks_pulled: int = 0
    tasks_imported: int = 0
    events_pushed: int = 0
    events_pulled: int = 0
    errors: List[str] = Field(default_factory=list)
    error: str = ''
