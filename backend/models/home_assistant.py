"""API shapes for Home Assistant (routers/home_assistant.py, omi-local): the user's connection
and event choices, and the log of service calls agents asked for. No shape carries the token."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

EventDetail = Literal['summary', 'minimal']
ActionStatus = Literal['refused', 'waiting_for_approval', 'running', 'declined', 'executed', 'failed', 'expired']


class HomeAssistantEvents(BaseModel):
    conversation_finished: Optional[bool] = None
    task_created: Optional[bool] = None
    task_due: Optional[bool] = None
    waiting_for_approval: Optional[bool] = None
    day_summary_ready: Optional[bool] = None


class HomeAssistantSettingsResponse(BaseModel):
    configured: bool = Field(description='An address and a token are set (the user\'s own or the stack\'s)')
    enabled: bool = True
    url: str = ''
    url_from_env: bool = Field(default=False, description='The address is the stack\'s OMI_HA_URL')
    has_token: bool = False
    token_from_env: bool = False
    person: str = Field(default='', description='The name sent with every event, for automations')
    events: Dict[str, bool] = Field(default_factory=dict, description='Event type -> fired for this user')
    event_detail: EventDetail = 'summary'
    read_allow: List[str] = Field(default_factory=list, description='Entities/domains agents may read (operator)')
    action_domains: List[str] = Field(default_factory=list, description='Domains agents may call (operator)')
    auto_approve: List[str] = Field(default_factory=list, description='Run without waiting for your OK (operator)')
    last_check_at: Optional[datetime] = None
    last_check_ok: Optional[bool] = None
    last_check_error: str = ''


class UpdateHomeAssistantSettingsRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=500, description='Own Home Assistant; empty: the stack\'s')
    token: Optional[str] = Field(
        default=None, max_length=1000, description='Long-lived access token; empty clears it; omitted keeps it'
    )
    enabled: Optional[bool] = None
    person: Optional[str] = Field(default=None, max_length=60)
    events: Optional[HomeAssistantEvents] = None
    event_detail: Optional[EventDetail] = None


class HomeAssistantTestResponse(BaseModel):
    ok: bool
    detail: str = ''
    error: str = ''


class HomeAssistantAction(BaseModel):
    id: str
    domain: str = ''
    service: str = ''
    entity_ids: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus = 'waiting_for_approval'
    sensitive: bool = Field(default=False, description='Lock, alarm, cover or garage: always asks')
    requested_by: str = ''
    decided_by: str = ''
    error: str = ''
    result_states: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class HomeAssistantActionsResponse(BaseModel):
    actions: List[HomeAssistantAction] = Field(default_factory=list, description='Newest first')
    waiting_count: int = 0


class HomeAssistantActionResponse(BaseModel):
    ok: bool
    action: Optional[HomeAssistantAction] = None
    error: str = ''
