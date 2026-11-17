"""Send to agent (omi-local): jobs for Odysseus (simple asks), Hermes (agentic work) and Studio
(coding agents), their approvals and results. See utils/agent_jobs.py.

The app shows ``POST /v1/agent-jobs/preview`` (exactly what would leave) before the user taps
Send; ``POST /v1/agent-jobs`` then sends that text. Studio reports results on the signed
webhook ``POST /v1/integrations/studio/events``.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import database.agent_jobs as jobs_db
from models.integrations_out import (
    AgentJob,
    AgentJobPreviewRequest,
    AgentJobPreviewResponse,
    AgentJobResponse,
    AgentJobsResponse,
    AnswerAgentApprovalRequest,
    CreateAgentJobRequest,
    StudioEventResponse,
)
from utils import agent_jobs
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter()


def _raise(error: agent_jobs.AgentJobError):
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.post('/v1/agent-jobs/preview', tags=['agent-jobs'], response_model=AgentJobPreviewResponse)
async def preview_agent_job(request: AgentJobPreviewRequest, uid: str = Depends(auth.get_current_user_uid)):
    """Exactly what Send would give the agent (the prompt and Omi's context), and where it goes."""
    try:
        return await run_blocking(db_executor, agent_jobs.build, uid, request.model_dump())
    except agent_jobs.AgentJobError as error:
        _raise(error)


@router.post('/v1/agent-jobs', tags=['agent-jobs'], response_model=AgentJobResponse)
async def create_agent_job(request: CreateAgentJobRequest, uid: str = Depends(auth.get_current_user_uid)):
    """Send the previewed text to the agent (the user's explicit OK) and follow the job."""
    try:
        job = await agent_jobs.create(uid, request.model_dump())
    except agent_jobs.AgentJobError as error:
        _raise(error)
    return AgentJobResponse(
        ok=job.get('status') != 'failed', job=AgentJob.model_validate(job), error=job.get('error') or ''
    )


@router.get('/v1/agent-jobs', tags=['agent-jobs'], response_model=AgentJobsResponse)
async def list_agent_jobs(limit: int = Query(50, ge=1, le=100), uid: str = Depends(auth.get_current_user_uid)):
    rows = await run_blocking(db_executor, jobs_db.list_recent, uid, limit)
    return AgentJobsResponse(jobs=[AgentJob.model_validate(r) for r in rows])


@router.get('/v1/agent-jobs/{job_id}', tags=['agent-jobs'], response_model=AgentJob)
async def get_agent_job(job_id: str, uid: str = Depends(auth.get_current_user_uid)):
    row = await run_blocking(db_executor, jobs_db.get, uid, job_id)
    if not row:
        raise HTTPException(status_code=404, detail='Job not found')
    return AgentJob.model_validate(row)


@router.post('/v1/agent-jobs/{job_id}/approval', tags=['agent-jobs'], response_model=AgentJobResponse)
async def answer_agent_job_approval(
    job_id: str, request: AnswerAgentApprovalRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Answer the agent's question (Hermes: allow once, or deny)."""
    try:
        job = await agent_jobs.answer_approval(uid, job_id, request.choice)
    except agent_jobs.AgentJobError as error:
        _raise(error)
    return AgentJobResponse(ok=True, job=AgentJob.model_validate(job))


@router.post('/v1/integrations/studio/events', tags=['agent-jobs'], response_model=StudioEventResponse)
async def studio_events(request: Request):
    """Studio's signed chat webhook (X-Hermes-Signature-256 over "<X-Hermes-Timestamp>.<body>")."""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    status, job_id = await agent_jobs.studio_event(raw, headers)
    if status != 200:
        return JSONResponse(status_code=status, content={'ok': False, 'job_id': ''})
    return StudioEventResponse(ok=True, job_id=job_id)
