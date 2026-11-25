"""API shapes for Omi's outbound integrations (omi-local): the Connections settings, sending a
conversation or a day to the wiki, Coinpile, and agent jobs (Odysseus, Hermes, Studio).
See utils/integrations_out.py for what may leave and when."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

KeptConversationsMode = Literal['off', 'ask', 'on_keep']
AgentTarget = Literal['odysseus', 'hermes', 'studio']
AgentJobStatus = Literal['queued', 'running', 'waiting_for_approval', 'completed', 'failed', 'cancelled']
StudioAgent = Literal['ekko-agent', 'hermes', 'claude-code', 'codex', 'pi', 'grok', 'opencode']
OutboundTargetName = Literal['wiki', 'hermes', 'odysseus', 'studio', 'coinpile', 'orb']

_URL = Field(default=None, max_length=500, description="http(s) address; '' clears it (the stack's default applies)")
_TOKEN = Field(default=None, max_length=4000, description="Stored encrypted, never returned; '' clears it")


class OutboundTargetBase(BaseModel):
    enabled: bool = False
    url: str = Field(default='', description='The address in use (the user\'s own, else the stack\'s default)')
    own_url: str = Field(default='', description='The address the user set ("" = the stack\'s default)')
    url_from_env: bool = Field(default=False, description='The address comes from the stack (env)')
    has_token: bool = False
    token_from_env: bool = Field(default=False, description='The token is the stack\'s household token (env)')
    last_check_at: Optional[datetime] = None
    last_check_ok: Optional[bool] = None
    last_check_error: str = ''


class WikiOutboundSettings(OutboundTargetBase):
    person: str = Field(
        default='', description='Wiki person to file under (for a shared token); empty = the token\'s owner'
    )
    send_day_reviews: bool = Field(default=True, description='Send each approved day summary as a wiki source')
    send_kept_conversations: KeptConversationsMode = Field(
        default='off',
        description="'off': only on Send; 'ask': offer Send on kept clips; 'on_keep': send a clip when the user keeps it",
    )
    include_transcript: bool = Field(default=False, description='Sent conversations include the transcript')
    require_manual_approval: bool = Field(
        default=True, description='Only a day the user approved by hand is sent (auto-approved days are not)'
    )


class HermesOutboundSettings(OutboundTargetBase):
    pass


class OdysseusOutboundSettings(OutboundTargetBase):
    endpoint_id: str = Field(default='', description="Odysseus model endpoint for new chats (Odysseus' Settings)")
    model: str = Field(default='', description='Model for new chats')
    session_id: str = Field(default='', description='Reuse this Odysseus chat for every ask (else one chat per ask)')


class StudioOutboundSettings(OutboundTargetBase):
    default_agent: StudioAgent = 'ekko-agent'
    workspace: str = Field(default='', description='Default workspace path for coding agents')
    has_webhook_secret: bool = False
    webhook_path: str = Field(default='/v1/integrations/studio/events', description='Register this in Studio')


class CoinpileOutboundSettings(OutboundTargetBase):
    pass


class OrbOutboundSettings(OutboundTargetBase):
    log_day_approvals: bool = Field(default=False, description='Add an Orb log line when a day is approved')
    project: str = Field(default='General', description='Orb project for that log line')
    require_manual_approval: bool = True


class OutboundIntegrationsResponse(BaseModel):
    wiki: WikiOutboundSettings = Field(default_factory=WikiOutboundSettings)
    hermes: HermesOutboundSettings = Field(default_factory=HermesOutboundSettings)
    odysseus: OdysseusOutboundSettings = Field(default_factory=OdysseusOutboundSettings)
    studio: StudioOutboundSettings = Field(default_factory=StudioOutboundSettings)
    coinpile: CoinpileOutboundSettings = Field(default_factory=CoinpileOutboundSettings)
    orb: OrbOutboundSettings = Field(default_factory=OrbOutboundSettings)
    conversation_webhook_to_wiki: bool = Field(
        default=False,
        description='The developer Conversation Events webhook points at the wiki: while Send to wiki is on, '
        'Omi skips that webhook so nothing is filed twice',
    )


class UpdateOutboundTarget(BaseModel):
    enabled: Optional[bool] = None
    url: Optional[str] = _URL
    token: Optional[str] = _TOKEN


class UpdateWikiOutbound(UpdateOutboundTarget):
    person: Optional[str] = Field(default=None, max_length=100)
    send_day_reviews: Optional[bool] = None
    send_kept_conversations: Optional[KeptConversationsMode] = None
    include_transcript: Optional[bool] = None
    require_manual_approval: Optional[bool] = None


class UpdateOdysseusOutbound(UpdateOutboundTarget):
    endpoint_id: Optional[str] = Field(default=None, max_length=200)
    model: Optional[str] = Field(default=None, max_length=200)
    session_id: Optional[str] = Field(default=None, max_length=200)


class UpdateStudioOutbound(UpdateOutboundTarget):
    default_agent: Optional[StudioAgent] = None
    workspace: Optional[str] = Field(default=None, max_length=500)
    webhook_secret: Optional[str] = _TOKEN


class UpdateOrbOutbound(UpdateOutboundTarget):
    log_day_approvals: Optional[bool] = None
    project: Optional[str] = Field(default=None, max_length=100)
    require_manual_approval: Optional[bool] = None


class UpdateOutboundIntegrationsRequest(BaseModel):
    wiki: Optional[UpdateWikiOutbound] = None
    hermes: Optional[UpdateOutboundTarget] = None
    odysseus: Optional[UpdateOdysseusOutbound] = None
    studio: Optional[UpdateStudioOutbound] = None
    coinpile: Optional[UpdateOutboundTarget] = None
    orb: Optional[UpdateOrbOutbound] = None


class OutboundTestResponse(BaseModel):
    ok: bool
    error: str = ''
    detail: str = Field(default='', description='What the probe checked')


# ---- what was sent where ---------------------------------------------------------------------


class OutboundReceipt(BaseModel):
    target: str
    sent_at: Optional[datetime] = None
    title: str = Field(default='', description='The title it was filed under (a re-send says "(revised n)")')
    revision: int = Field(default=0, description='0 for the first copy, n for the n-th revised copy')
    detail: str = Field(default='', description="What the target answered, e.g. the wiki file's path")
    error: str = Field(default='', description='Why it was not sent (then sent_at is empty)')
    scheduled_at: Optional[datetime] = Field(
        default=None, description='Not sent yet: it leaves then, if the approval still stands (Undo stops it)'
    )


class SendConversationRequest(BaseModel):
    target: Literal['wiki'] = 'wiki'
    revised: bool = Field(
        default=False, description='Send again although a copy was sent already (filed as "(revised n)")'
    )


class SendConversationResponse(BaseModel):
    ok: bool
    receipt: Optional[OutboundReceipt] = None
    error: str = ''
    already_sent: bool = Field(default=False, description='A copy was sent before; repeat with revised=true')


class OutboundPreviewResponse(BaseModel):
    target: str
    title: str = ''
    content: str = Field(default='', description='Exactly what would leave Omi')
    destination: str = Field(default='', description='Where it would go, e.g. the wiki address')
    receipts: List[OutboundReceipt] = Field(default_factory=list, description='Copies already sent')


class CoinpileSendRequest(BaseModel):
    kind: Literal['note', 'transaction'] = 'note'
    conversation_id: Optional[str] = Field(default=None, max_length=128)
    task_id: Optional[str] = Field(default=None, max_length=128)
    title: str = Field(default='', max_length=200)
    body: str = Field(default='', max_length=20000)
    account_id: Optional[str] = Field(default=None, max_length=200, description='Coinpile account (ff:...)')
    amount: Optional[float] = Field(default=None, description='Negative = money out')
    description: Optional[str] = Field(default=None, max_length=500)
    date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    category: Optional[str] = Field(default=None, max_length=100)


class CoinpileSendResponse(BaseModel):
    ok: bool
    error: str = ''
    answer: str = Field(default='', description="Coinpile's answer (a change waits for a Yes on Coinpile's Agent page)")


class CoinpileAccount(BaseModel):
    id: str
    name: str = ''
    type: str = ''


class CoinpileAccountsResponse(BaseModel):
    accounts: List[CoinpileAccount] = Field(default_factory=list)
    error: str = ''


# ---- agent jobs ------------------------------------------------------------------------------


class AgentJobSection(BaseModel):
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    section_id: str = Field(max_length=128)


class AgentJobContext(BaseModel):
    conversation_ids: List[str] = Field(default_factory=list, max_length=20)
    section: Optional[AgentJobSection] = None
    task_id: Optional[str] = Field(default=None, max_length=128)


class AgentJobPreviewRequest(AgentJobContext):
    target: AgentTarget
    prompt: str = Field(default='', max_length=20000)
    agent_id: Optional[StudioAgent] = None
    include_transcript: bool = False


class AgentJobPreviewResponse(BaseModel):
    target: str
    destination: str = ''
    content: str = Field(default='', description='Exactly what would leave Omi: the prompt and its context')
    suggested_target: AgentTarget = Field(default='odysseus', description='Odysseus for a short ask, Hermes for a job')
    title: str = ''


class CreateAgentJobRequest(AgentJobPreviewRequest):
    title: str = Field(default='', max_length=200)
    attach_result: bool = Field(default=False, description='Add the result to the first conversation as a note')


class AgentJobApproval(BaseModel):
    request_id: str = ''
    command: str = Field(default='', description='What the agent asks to run (redacted by the agent)')
    description: str = ''
    choices: List[str] = Field(default_factory=list)


class AgentJob(BaseModel):
    id: str
    target: AgentTarget
    title: str = ''
    prompt: str = ''
    agent_id: str = ''
    status: AgentJobStatus = 'queued'
    external_id: str = Field(default='', description='Hermes run id, Studio chat session id, or Odysseus chat id')
    result: str = ''
    error: str = ''
    approval: Optional[AgentJobApproval] = None
    conversation_ids: List[str] = Field(default_factory=list)
    section: Optional[AgentJobSection] = None
    task_id: Optional[str] = None
    attach_result: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class AgentJobsResponse(BaseModel):
    jobs: List[AgentJob] = Field(default_factory=list)


class AgentJobResponse(BaseModel):
    ok: bool
    job: Optional[AgentJob] = None
    error: str = ''


class AnswerAgentApprovalRequest(BaseModel):
    choice: Literal['once', 'deny'] = Field(description='Allow this once, or refuse')


class StudioEventResponse(BaseModel):
    ok: bool
    job_id: str = ''


class StudioApproval(BaseModel):
    """Agent work in a Studio chat Omi started, waiting for an approval (Studio's AgentWork)."""

    id: str
    title: str = ''
    agent: str = Field(default='', description='The Studio agent doing the work')
    summary: str = Field(default='', description='What the agent says it did or wants to do')
    status: str = Field(default='', description="Studio's state: 'awaiting_approval' while it waits")
    session_id: str = Field(default='', description='The Studio chat')
    decision: str = Field(default='', description="'approved' or 'rejected' once someone answered")
    decided_via: str = Field(default='', description='Where it was answered: studio, mattermost, orb or omi')
    decided_by: str = Field(default='', description='Who answered')
    job_id: str = Field(default='', description="The Omi agent job that started this chat, if it is one of the user's")
    job_title: str = ''
    created_at: Optional[datetime] = None


class StudioApprovalsResponse(BaseModel):
    approvals: List[StudioApproval] = Field(default_factory=list)
    error: str = Field(default='', description="Why Studio's approvals could not be read (Studio itself is on)")


class AnswerStudioApprovalRequest(BaseModel):
    decision: Literal['approve', 'reject']
    by: Optional[str] = Field(default=None, max_length=60, description='Who answers, as Studio shows it')


class StudioApprovalAnswerResponse(BaseModel):
    ok: bool = Field(description='This answer was the one that counted')
    changed: bool = False
    already_answered: bool = Field(
        default=False, description='Someone answered first, or the work moved on: `message` says who or why'
    )
    message: str = Field(default='', description="Studio's words, e.g. \"Already approved in Studio by Robin.\"")
    approval: Optional[StudioApproval] = None
