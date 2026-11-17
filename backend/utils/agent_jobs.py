"""Send to agent (omi-local): a conversation, a day-review section or a task, with the user's
prompt, goes to one of three agents, and the result comes back to Omi.

Routing (the app suggests, the user picks):

- **Odysseus** (Wizard815/odysseus) for simple things: a quick question, a short summary, a
  reminder, a look-up. One chat turn: ``POST /api/session`` (form: name, endpoint_id, model;
  a Bearer ``ody_`` token may not pass a raw endpoint URL, routes/session_routes.py) unless the
  user set a chat to reuse, then ``POST /api/chat {message, session}`` -> ``{response}``
  (routes/chat_routes.py chat_endpoint, token scope "chat"). Synchronous, so Omi runs it in the
  background and stores the answer.
- **Hermes** (Hermes Agent's API server) for agentic work: ``POST /v1/runs {input,
  session_id, instructions}`` with ``Idempotency-Key: omi-job-<id>`` returns a run id at once
  (gateway/platforms/api_server_runs.py); Omi polls ``GET /v1/runs/{id}``. A run that stops at
  ``waiting_for_approval`` is kept (unlike the chat bridge, which stops it): the question shows in
  Omi's Approvals and the user's answer goes to ``POST /v1/runs/{id}/approval {choice, request_id}``.
- **Studio** (Ekko / Hermes Studio) for coding agents: ``POST /api/studio/orb/work {agentId,
  title, prompt, workspace?}`` (admin JWT) starts a Studio chat; its result arrives on Omi's
  signed webhook (``POST /v1/integrations/studio/events``, X-Hermes-Signature-256 =
  sha256 HMAC of "<timestamp>.<body>", services/webhooks/dispatcher.ts), and Omi also polls
  ``GET /api/studio/orb/work`` for the status. A Studio approval is answered in Studio.

herdr is not a target: Studio's coding agents or a Hermes skill on the herdr host reach it.

The preview the app shows is exactly what leaves (``build``); the job keeps that text. The
queue (database/outbound_work.py ``job:{uid}:{id}``) follows the job until it ends; the end
is pushed to the user's phone, and optionally added to the first conversation as a note.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import database.action_items as action_items_db
import database.agent_jobs as jobs_db
import database.conversations as conversations_db
import database.day_reviews as day_reviews_db
import database.notifications as notification_db
import database.outbound_work as work_db
from models.conversation import Conversation
from utils import day_review_service, integrations_out
from utils.executors import db_executor, run_blocking, start_background_task
from utils.notifications import send_notification
from utils.outbound_http import OutboundError, error_text, request_ok

logger = logging.getLogger(__name__)

TARGETS = ('odysseus', 'hermes', 'studio')
MAX_CHARS = {'odysseus': 50_000, 'hermes': 100_000, 'studio': 20_000}
TERMINAL = ('completed', 'failed', 'cancelled')
POLL_EVERY = timedelta(seconds=15)
GIVE_UP_AFTER = timedelta(hours=24)
ODYSSEUS_TIMEOUT_SECONDS = 180.0
ODYSSEUS_STALE_CLAIM = timedelta(minutes=10)
STUDIO_SIGNATURE_MAX_AGE_SECONDS = 300
SHORT_ASK_CHARS = 300
RESULT_MAX_CHARS = 20_000

HERMES_INSTRUCTIONS = (
    'This job comes from Omi, the user\'s wearable memory assistant; the user read and approved exactly '
    'this text before it was sent. Do the job. Ask for approval before anything risky. Finish with a '
    'short answer the user can read on their phone: what you did and the result.'
)

_running: Set[str] = set()


class AgentJobError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def job_key(uid: str, job_id: str) -> str:
    return f'job:{uid}:{job_id}'


def studio_key(session_id: str) -> str:
    return f'studio:{session_id}'


# ---- what leaves -----------------------------------------------------------------------------


def _zone(uid: str):
    return day_review_service.timezone_for(notification_db.get_user_time_zone(uid))


def _local(value: Any, zone: Any) -> str:
    if not isinstance(value, datetime):
        return ''
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone).strftime('%Y-%m-%d %H:%M')


def _conversation_block(uid: str, conversation_id: str, zone: Any, include_transcript: bool) -> Tuple[str, str]:
    row = conversations_db.get_conversation(uid, conversation_id)
    if not row or row.get('deleted') or row.get('is_locked'):
        raise AgentJobError(404, 'Conversation not found')
    conversation = Conversation(**row)
    structured = conversation.structured
    title = str(getattr(structured, 'title', '') or '').strip() or 'Conversation'
    lines = [f'## Conversation: {title} ({_local(conversation.started_at or conversation.created_at, zone)})', '']
    overview = str(getattr(structured, 'overview', '') or '').strip()
    if overview:
        lines += [overview, '']
    items = [a for a in (getattr(structured, 'action_items', None) or []) if getattr(a, 'description', '')]
    if items:
        lines += ['Action items:'] + [f'- {a.description}' for a in items] + ['']
    if include_transcript:
        transcript = day_review_service._transcript(conversation).strip()  # noqa: SLF001 — the review's own reader
        if transcript:
            lines += ['Transcript:', transcript, '']
    return title, '\n'.join(lines).strip()


def _section_block(uid: str, date_str: str, section_id: str) -> Tuple[str, str]:
    stored = day_reviews_db.get_day_review(uid, date_str)
    if not stored:
        raise AgentJobError(404, 'Day summary not found')
    review = day_review_service.present({**stored, 'date': date_str})
    section = next((s for s in review.get('sections') or [] if s.get('id') == section_id), None)
    if section is None:
        raise AgentJobError(404, 'Section not found')
    title = str(section.get('title') or 'Part of the day')
    return title, f"## {date_str}: {title}\n\n{str(section.get('text') or '').strip()}"


def _task_block(uid: str, task_id: str, zone: Any) -> Tuple[str, str]:
    task = action_items_db.get_action_item(uid, task_id)
    if not task:
        raise AgentJobError(404, 'Task not found')
    description = str(task.get('description') or '').strip()
    due = _local(task.get('due_at'), zone)
    return description[:80] or 'Task', f'## Task\n\n{description}' + (f'\nDue: {due}' if due else '')


def suggest_target(prompt: str) -> str:
    """Odysseus for a short, simple ask; Hermes for a job (the user can always change it)."""
    return 'odysseus' if len((prompt or '').strip()) <= SHORT_ASK_CHARS else 'hermes'


def build(uid: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """Exactly what would leave Omi for this request (the preview, and later the job's text)."""
    target = request['target']
    if target not in TARGETS:
        raise AgentJobError(422, 'Unknown agent')
    zone = _zone(uid)
    blocks: List[str] = []
    titles: List[str] = []
    for cid in request.get('conversation_ids') or []:
        title, block = _conversation_block(uid, cid, zone, bool(request.get('include_transcript')))
        titles.append(title)
        blocks.append(block)
    if request.get('section'):
        title, block = _section_block(uid, request['section']['date'], request['section']['section_id'])
        titles.append(title)
        blocks.append(block)
    if request.get('task_id'):
        title, block = _task_block(uid, request['task_id'], zone)
        titles.append(title)
        blocks.append(block)
    prompt = str(request.get('prompt') or '').strip()
    if not prompt and not blocks:
        raise AgentJobError(422, 'Write what the agent should do')
    content = prompt
    if blocks:
        content = (prompt + '\n\n' if prompt else '') + '---\nContext from Omi:\n\n' + '\n\n'.join(blocks)
    limit = MAX_CHARS[target]
    if len(content) > limit:
        content = content[: limit - 40].rstrip() + '\n\n[context cut to fit]'
    settings = integrations_out.effective(uid)[target]
    agent_id = request.get('agent_id') or (settings.get('default_agent') if target == 'studio' else '') or ''
    destination = settings.get('url') or '(no address set)'
    if target == 'studio':
        destination += f' (agent {agent_id})'
    title = str(request.get('title') or '').strip() or (titles[0] if titles else prompt[:60]) or 'Omi job'
    return {
        'target': target,
        'destination': destination,
        'content': content,
        'suggested_target': suggest_target(prompt),
        'title': title[:200],
        'agent_id': agent_id,
    }


# ---- starting --------------------------------------------------------------------------------


def _settings(uid: str, target: str) -> Dict[str, Any]:
    settings = integrations_out.effective(uid)[target]
    if not integrations_out.ready(settings):
        raise AgentJobError(
            409, f'{target.capitalize()} is off, or its address or token is missing (Settings > Connections)'
        )
    return settings


async def create(uid: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """The user's explicit Send: record the job with exactly the previewed text, then start it."""
    settings = await run_blocking(db_executor, _settings, uid, request['target'])
    built = await run_blocking(db_executor, build, uid, request)
    job_id = uuid.uuid4().hex
    job = {
        'target': built['target'],
        'title': built['title'],
        'prompt': built['content'],
        'agent_id': built['agent_id'],
        'status': 'queued',
        'external_id': '',
        'result': '',
        'error': '',
        'approval': None,
        'conversation_ids': list(request.get('conversation_ids') or []),
        'section': request.get('section'),
        'task_id': request.get('task_id'),
        'attach_result': bool(request.get('attach_result')),
    }
    job = await run_blocking(db_executor, jobs_db.create, uid, job_id, job)
    now = datetime.now(timezone.utc)
    try:
        if job['target'] == 'hermes':
            run_id = await _hermes_start(settings, job)
            await _set(uid, job_id, status='running', external_id=run_id)
        elif job['target'] == 'studio':
            session_id = await _studio_start(settings, job)
            await _set(uid, job_id, status='running', external_id=session_id)
            await run_blocking(
                db_executor, work_db.put, studio_key(session_id), {'kind': 'studio_ref', 'uid': uid, 'job_id': job_id}
            )
    except OutboundError as error:
        await _finish(uid, job_id, 'failed', error=error.message)
        return await run_blocking(db_executor, jobs_db.get, uid, job_id) or job
    await run_blocking(
        db_executor,
        work_db.put,
        job_key(uid, job_id),
        {'kind': 'job', 'uid': uid, 'job_id': job_id, 'due_at': now, 'started_at': now},
    )
    if job['target'] == 'odysseus':
        # A simple ask should answer in seconds, not at the next tick.
        start_background_task(
            follow({'key': job_key(uid, job_id), 'uid': uid, 'job_id': job_id, 'started_at': now}, now),
            name='agent_job_odysseus',
        )
    return await run_blocking(db_executor, jobs_db.get, uid, job_id) or job


async def _hermes_start(settings: Dict[str, Any], job: Dict[str, Any]) -> str:
    body = await request_ok(
        'POST',
        f"{settings['url']}/v1/runs",
        headers={'Authorization': f"Bearer {settings['token']}", 'Idempotency-Key': f"omi-job-{job['id']}"},
        json_body={'input': job['prompt'], 'session_id': f"omi-job-{job['id']}", 'instructions': HERMES_INSTRUCTIONS},
    )
    run_id = str((body or {}).get('run_id') or '') if isinstance(body, dict) else ''
    if not run_id:
        raise OutboundError('http', 'Hermes did not return a run id')
    return run_id


async def _studio_start(settings: Dict[str, Any], job: Dict[str, Any]) -> str:
    payload: Dict[str, Any] = {
        'agentId': job.get('agent_id') or 'ekko-agent',
        'title': job['title'][:80],
        'prompt': job['prompt'],
    }
    if settings.get('workspace'):
        payload['workspace'] = settings['workspace']
    body = await request_ok(
        'POST',
        f"{settings['url']}/api/studio/orb/work",
        headers={'Authorization': f"Bearer {settings['token']}"},
        json_body=payload,
        timeout=60.0,
    )
    session_id = str(((body or {}).get('work') or {}).get('sessionId') or '') if isinstance(body, dict) else ''
    if not session_id:
        raise OutboundError('http', 'Studio did not return a chat id')
    return session_id


# ---- following -------------------------------------------------------------------------------


async def _set(uid: str, job_id: str, **fields: Any) -> None:
    await run_blocking(db_executor, jobs_db.update, uid, job_id, fields)


def _notify(uid: str, title: str, body: str, job_id: str) -> None:
    try:
        send_notification(
            uid, title, body[:240], data={'type': 'agent_job', 'job_id': job_id, 'navigate_to': '/agent-jobs'}
        )
    except Exception as error:  # noqa: BLE001 — no push configured is fine; the job page shows it
        logger.info('agent_jobs: push for %s not sent: %s', job_id, type(error).__name__)


def _attach(uid: str, job: Dict[str, Any], result: str) -> None:
    ids = job.get('conversation_ids') or []
    if not job.get('attach_result') or not ids:
        return
    row = conversations_db.get_conversation(uid, ids[0])
    if not row:
        return
    results = list(row.get('apps_results') or [])
    results.append(
        {
            'app_id': f"agent-job-{job['target']}",
            'content': f"{job.get('title') or 'Agent job'} ({job['target']})\n\n{result}",
        }
    )
    conversations_db.update_conversation(uid, ids[0], {'apps_results': results})


async def _finish(uid: str, job_id: str, status: str, *, result: str = '', error: str = '') -> None:
    now = datetime.now(timezone.utc)
    await _set(
        uid, job_id, status=status, result=result[:RESULT_MAX_CHARS], error=error[:500], approval=None, finished_at=now
    )
    job = await run_blocking(db_executor, jobs_db.get, uid, job_id) or {}
    await run_blocking(db_executor, work_db.delete, job_key(uid, job_id))
    if job.get('target') == 'studio' and job.get('external_id'):
        await run_blocking(db_executor, work_db.delete, studio_key(job['external_id']))
    agent = str(job.get('target') or 'Agent').capitalize()
    if status == 'completed':
        if result:
            await run_blocking(db_executor, _attach, uid, job, result)
        await run_blocking(
            db_executor, _notify, uid, f'{agent} finished: {job.get("title") or "your job"}', result or 'Done', job_id
        )
    else:
        await run_blocking(
            db_executor,
            _notify,
            uid,
            f'{agent} could not finish: {job.get("title") or "your job"}',
            error or status,
            job_id,
        )


async def _ask(uid: str, job: Dict[str, Any], approval: Dict[str, Any]) -> None:
    """Park the job on the agent's question; push once per question."""
    previous = job.get('approval') or {}
    await _set(uid, job['id'], status='waiting_for_approval', approval=approval)
    if previous.get('request_id') != approval.get('request_id') or job.get('status') != 'waiting_for_approval':
        agent = str(job.get('target') or 'Agent').capitalize()
        await run_blocking(
            db_executor,
            _notify,
            uid,
            f'{agent} asks before it goes on',
            approval.get('description') or approval.get('command') or job.get('title') or '',
            job['id'],
        )


async def _follow_odysseus(uid: str, job: Dict[str, Any], settings: Dict[str, Any], now: datetime) -> str:
    claimed = job.get('claimed_at')
    if (
        job.get('status') == 'running'
        and isinstance(claimed, datetime)
        and now - _aware(claimed) < ODYSSEUS_STALE_CLAIM
    ):
        return 'running'
    if job.get('status') == 'running':
        await _finish(uid, job['id'], 'failed', error='Odysseus did not answer (interrupted)')
        return 'failed'
    await _set(uid, job['id'], status='running', claimed_at=now)
    auth = {'Authorization': f"Bearer {settings['token']}"}
    session = settings.get('session_id') or ''
    if not session:
        if not settings.get('endpoint_id'):
            await _finish(
                uid, job['id'], 'failed', error='Set the Odysseus model endpoint (Settings > Connections > Odysseus)'
            )
            return 'failed'
        form = {
            'name': f"Omi: {job.get('title') or 'ask'}"[:200],
            'endpoint_id': settings['endpoint_id'],
            'model': settings.get('model') or '',
        }
        created = await request_ok('POST', f"{settings['url']}/api/session", headers=auth, form=form)
        session = str((created or {}).get('id') or '') if isinstance(created, dict) else ''
        if not session:
            raise OutboundError('http', 'Odysseus did not return a chat id')
        await _set(uid, job['id'], external_id=session)
    answer = await request_ok(
        'POST',
        f"{settings['url']}/api/chat",
        headers=auth,
        json_body={'message': job['prompt'], 'session': session},
        timeout=ODYSSEUS_TIMEOUT_SECONDS,
    )
    reply = str((answer or {}).get('response') or '') if isinstance(answer, dict) else str(answer or '')
    await _finish(uid, job['id'], 'completed', result=reply)
    return 'completed'


async def _follow_hermes(uid: str, job: Dict[str, Any], settings: Dict[str, Any]) -> str:
    status = await request_ok(
        'GET',
        f"{settings['url']}/v1/runs/{job['external_id']}",
        headers={'Authorization': f"Bearer {settings['token']}"},
    )
    if not isinstance(status, dict):
        raise OutboundError('http', 'Hermes answered no run status')
    state = str(status.get('status') or '')
    if state == 'waiting_for_approval':
        raw = status.get('approval') or {}
        approval = {
            'request_id': str(raw.get('request_id') or ''),
            'command': str(raw.get('command') or ''),
            'description': str(raw.get('description') or ''),
            'choices': [c for c in (raw.get('choices') or []) if c in ('once', 'deny')] or ['once', 'deny'],
        }
        await _ask(uid, job, approval)
        return 'waiting_for_approval'
    if state == 'completed':
        await _finish(uid, job['id'], 'completed', result=str(status.get('output') or ''))
        return 'completed'
    if state in ('failed', 'cancelled'):
        await _finish(uid, job['id'], state, error=error_text(status.get('error') or state))
        return state
    if job.get('status') != 'running':
        await _set(uid, job['id'], status='running', approval=None)
    return 'running'


async def _follow_studio(uid: str, job: Dict[str, Any], settings: Dict[str, Any]) -> str:
    body = await request_ok(
        'GET', f"{settings['url']}/api/studio/orb/work", headers={'Authorization': f"Bearer {settings['token']}"}
    )
    items = (body or {}).get('work') if isinstance(body, dict) else []
    item = next((w for w in items or [] if isinstance(w, dict) and w.get('sessionId') == job['external_id']), None)
    if item is None:
        return 'running'
    state = str(item.get('status') or '')
    if state == 'blocked':
        await _ask(
            uid,
            job,
            {
                'request_id': f"studio-{job['external_id']}",
                'command': '',
                'description': 'Studio is waiting for you: answer it in Studio',
                'choices': [],
            },
        )
        return 'waiting_for_approval'
    if state == 'done':
        # The signed webhook carries the text; without it, say where the result is.
        await _finish(
            uid,
            job['id'],
            'completed',
            result=job.get('result') or 'Finished in Studio: open the chat there to read the result.',
        )
        return 'completed'
    if state == 'gone' or item.get('error'):
        await _finish(uid, job['id'], 'failed', error=str(item.get('error') or 'Studio stopped following the chat'))
        return 'failed'
    if job.get('status') != 'running':
        await _set(uid, job['id'], status='running', approval=None)
    return 'running'


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def follow(item: Dict[str, Any], now: datetime) -> str:
    """One look at a job (from the queue tick, or right after an Odysseus ask)."""
    uid, job_id = item['uid'], item['job_id']
    if job_id in _running:
        return 'busy'
    _running.add(job_id)
    try:
        job = await run_blocking(db_executor, jobs_db.get, uid, job_id)
        if not job or job.get('status') in TERMINAL:
            await run_blocking(db_executor, work_db.delete, job_key(uid, job_id))
            return 'gone'
        started = item.get('started_at')
        if isinstance(started, datetime) and now - _aware(started) > GIVE_UP_AFTER:
            await _finish(uid, job_id, 'failed', error='No result after 24 hours')
            return 'failed'
        settings = (await run_blocking(db_executor, integrations_out.effective, uid))[job['target']]
        if not integrations_out.ready(settings):
            await _finish(uid, job_id, 'failed', error=f"{job['target'].capitalize()} was turned off")
            return 'failed'
        try:
            if job['target'] == 'odysseus':
                outcome = await _follow_odysseus(uid, job, settings, now)
            elif job['target'] == 'hermes':
                outcome = await _follow_hermes(uid, job, settings)
            else:
                outcome = await _follow_studio(uid, job, settings)
        except OutboundError as error:
            if job['target'] == 'odysseus':
                await _finish(uid, job_id, 'failed', error=error.message)
                return 'failed'
            await _set(uid, job_id, error=error.message)
            outcome = 'retry'
        if outcome not in TERMINAL:
            await run_blocking(db_executor, work_db.update, job_key(uid, job_id), {'due_at': now + POLL_EVERY})
        return outcome
    finally:
        _running.discard(job_id)


# ---- the user's answers, and Studio's results --------------------------------------------------


async def answer_approval(uid: str, job_id: str, choice: str) -> Dict[str, Any]:
    """Relay the user's answer to Hermes (once or deny). Studio's questions are answered in Studio."""
    job = await run_blocking(db_executor, jobs_db.get, uid, job_id)
    if not job:
        raise AgentJobError(404, 'Job not found')
    if job.get('status') != 'waiting_for_approval' or not job.get('approval'):
        raise AgentJobError(409, 'This job is not waiting for an answer')
    if job['target'] != 'hermes':
        raise AgentJobError(409, 'Answer this one in Studio')
    settings = await run_blocking(db_executor, _settings, uid, 'hermes')
    body: Dict[str, Any] = {'choice': choice}
    if job['approval'].get('request_id'):
        body['request_id'] = job['approval']['request_id']
    try:
        await request_ok(
            'POST',
            f"{settings['url']}/v1/runs/{job['external_id']}/approval",
            headers={'Authorization': f"Bearer {settings['token']}"},
            json_body=body,
        )
    except OutboundError as error:
        raise AgentJobError(502, error.message)
    await _set(uid, job_id, status='running', approval=None, answered=choice)
    await run_blocking(db_executor, work_db.update, job_key(uid, job_id), {'due_at': datetime.now(timezone.utc)})
    return await run_blocking(db_executor, jobs_db.get, uid, job_id) or job


def waiting(uid: str) -> List[Dict[str, Any]]:
    """Jobs whose agent waits for the user (for Omi's Approvals)."""
    return [j for j in jobs_db.list_recent(uid, limit=50) if j.get('status') == 'waiting_for_approval']


def verify_studio_signature(
    secret: str, timestamp: str, raw_body: bytes, signature: str, *, now: Optional[float] = None
) -> bool:
    """Studio's X-Hermes-Signature-256: 'sha256=' + HMAC-SHA256(secret, '<timestamp>.<raw body>')."""
    if not secret or not timestamp or not signature:
        return False
    try:
        age = abs((now if now is not None else time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > STUDIO_SIGNATURE_MAX_AGE_SECONDS:
        return False
    expected = 'sha256=' + hmac.new(secret.encode(), f'{timestamp}.'.encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


async def studio_event(raw_body: bytes, headers: Dict[str, str]) -> Tuple[int, str]:
    """A signed Studio webhook. Returns (http status, job id). Unknown chats and bad signatures
    look the same (401) to the sender."""
    try:
        event = json.loads(raw_body.decode() or '{}')
    except ValueError:
        return 400, ''
    session_id = str(((event.get('subject') or {}) if isinstance(event, dict) else {}).get('session_id') or '')
    ref = await run_blocking(db_executor, work_db.get, studio_key(session_id)) if session_id else None
    if not ref:
        return 401, ''
    uid, job_id = ref['uid'], ref['job_id']
    settings = (await run_blocking(db_executor, integrations_out.effective, uid))['studio']
    if not verify_studio_signature(
        settings.get('webhook_secret') or '',
        headers.get('x-hermes-timestamp', ''),
        raw_body,
        headers.get('x-hermes-signature-256', ''),
    ):
        return 401, ''
    kind = str(event.get('type') or '')
    job = await run_blocking(db_executor, jobs_db.get, uid, job_id)
    if not job or job.get('status') in TERMINAL:
        return 200, job_id
    if kind == 'chat.run.completed':
        text = str((event.get('message') or {}).get('text') or '')
        await _finish(
            uid, job_id, 'completed', result=text or 'Finished in Studio: open the chat there to read the result.'
        )
    elif kind == 'chat.run.failed':
        await _finish(
            uid, job_id, 'failed', error=str((event.get('summary') or {}).get('error_kind') or 'Studio run failed')
        )
    elif kind == 'chat.approval.requested':
        await _ask(
            uid,
            job,
            {
                'request_id': str((event.get('subject') or {}).get('approval_id') or ''),
                'command': str((event.get('summary') or {}).get('tool_name') or ''),
                'description': 'Studio asks for approval: answer it in Studio',
                'choices': [],
            },
        )
    return 200, job_id
