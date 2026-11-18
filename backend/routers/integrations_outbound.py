"""Connections (omi-local): where Omi may send the user's things, and the explicit sends.

GET/PUT /v1/integrations/outbound hold the user's targets (wiki, Hermes, Odysseus, Studio,
Coinpile, Pondering Orb); tokens are write-only. POST .../{target}/test is the functional
probe behind "Test connection". What leaves and when: utils/integrations_out.py.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Path, Query

import database.integrations_out as outbound_db
from database.redis_db import get_user_webhook_db
from models.users import WebhookType
from models.integrations_out import (
    CoinpileAccount,
    CoinpileAccountsResponse,
    CoinpileSendRequest,
    CoinpileSendResponse,
    OutboundIntegrationsResponse,
    OutboundReceipt,
    OutboundPreviewResponse,
    OutboundTestResponse,
    SendConversationRequest,
    SendConversationResponse,
    UpdateOutboundIntegrationsRequest,
)
from utils import integrations_out, mcp_targets, wiki_outbound
from utils.outbound_http import OutboundError
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter()

_TARGET = Path(..., pattern=r'^(wiki|hermes|odysseus|studio|coinpile|orb)$')


def _conversation_webhook_url(uid: str) -> str:
    try:
        return get_user_webhook_db(uid, WebhookType.memory_created) or ''
    except Exception:  # noqa: BLE001 — Redis is fail-open; the notice is a bonus
        return ''


def _present(uid: str) -> OutboundIntegrationsResponse:
    return OutboundIntegrationsResponse.model_validate(
        integrations_out.present(uid, conversation_webhook_url=_conversation_webhook_url(uid))
    )


@router.get('/v1/integrations/outbound', tags=['integrations-outbound'], response_model=OutboundIntegrationsResponse)
async def get_outbound_integrations(uid: str = Depends(auth.get_current_user_uid)):
    """Where Omi may send this user's things. Tokens are never returned (``has_token``)."""
    return await run_blocking(db_executor, _present, uid)


@router.put('/v1/integrations/outbound', tags=['integrations-outbound'], response_model=OutboundIntegrationsResponse)
async def update_outbound_integrations(
    request: UpdateOutboundIntegrationsRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Change one or more targets. A token is stored encrypted; ``""`` clears it, omitted keeps it."""
    changes: Dict[str, Any] = {}
    for name in integrations_out.TARGETS:
        part = getattr(request, name)
        if part is None:
            continue
        data = part.model_dump(exclude_none=True)
        secrets = {key: data.pop(key) for key in ('token', 'webhook_secret') if key in data}
        if 'url' in data:
            try:
                data['url'] = integrations_out.clean_url(data['url'])
            except ValueError as error:
                raise HTTPException(status_code=422, detail=f'{name}: {error}')
        changes[name] = (data, secrets)

    def _save() -> OutboundIntegrationsResponse:
        for name, (data, secrets) in changes.items():
            outbound_db.set_target(uid, name, data, secrets=secrets)
        return _present(uid)

    return await run_blocking(db_executor, _save)


@router.post(
    '/v1/integrations/outbound/{target}/test', tags=['integrations-outbound'], response_model=OutboundTestResponse
)
async def test_outbound_integration(target: str = _TARGET, uid: str = Depends(auth.get_current_user_uid)):
    """Reach the target with the effective address and token (a real call that changes nothing)."""
    ok, error, detail = await integrations_out.test_connection(uid, target)
    await run_blocking(db_executor, lambda: outbound_db.record_check(uid, target, ok=ok, error=error))
    return OutboundTestResponse(ok=ok, error=error, detail=detail)


# ---- Send to wiki: one conversation, on the user's tap --------------------------------------


def _not_ready(error: Exception) -> HTTPException:
    message = str(error)
    return HTTPException(status_code=404 if 'not found' in message.lower() else 409, detail=message)


@router.get(
    '/v1/conversations/{conversation_id}/send/preview',
    tags=['integrations-outbound'],
    response_model=OutboundPreviewResponse,
)
async def preview_send_conversation(
    conversation_id: str,
    target: str = Query('wiki', pattern=r'^wiki$'),
    uid: str = Depends(auth.get_current_user_uid),
):
    """Exactly what Send to wiki would file (title, markdown, where), and the copies already sent."""
    try:
        return await wiki_outbound.preview_conversation(uid, conversation_id)
    except wiki_outbound.WikiNotReady as error:
        raise _not_ready(error)


@router.post(
    '/v1/conversations/{conversation_id}/send', tags=['integrations-outbound'], response_model=SendConversationResponse
)
async def send_conversation(
    conversation_id: str, request: SendConversationRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Send one conversation, as the user edited it, to the wiki (the user's explicit OK). A second
    copy needs ``revised: true`` and is filed as "(revised n)"."""
    try:
        receipt, already = await wiki_outbound.send_conversation(uid, conversation_id, revised_copy=request.revised)
    except wiki_outbound.WikiNotReady as error:
        raise _not_ready(error)
    if already:
        return SendConversationResponse(ok=False, already_sent=True, error='Already sent to the wiki')
    assert receipt is not None
    return SendConversationResponse(
        ok=not receipt.get('error'), receipt=OutboundReceipt.model_validate(receipt), error=receipt.get('error') or ''
    )


# ---- Send to Coinpile ------------------------------------------------------------------------


@router.get(
    '/v1/integrations/coinpile/accounts', tags=['integrations-outbound'], response_model=CoinpileAccountsResponse
)
async def coinpile_accounts(uid: str = Depends(auth.get_current_user_uid)):
    """The user's Coinpile accounts, for a transaction (Coinpile's read-only list_accounts)."""
    try:
        rows = await mcp_targets.coinpile_accounts(uid)
        return CoinpileAccountsResponse(accounts=[CoinpileAccount(**row) for row in rows])
    except (mcp_targets.TargetNotReady, OutboundError) as error:
        return CoinpileAccountsResponse(error=getattr(error, 'message', str(error)))


@router.get('/v1/integrations/coinpile/draft', tags=['integrations-outbound'], response_model=CoinpileSendRequest)
async def coinpile_draft(
    conversation_id: str = Query(None, max_length=128),
    task_id: str = Query(None, max_length=128),
    uid: str = Depends(auth.get_current_user_uid),
):
    """A note drafted from the conversation or task, for the user to edit before sending."""
    try:
        draft = await run_blocking(db_executor, mcp_targets.coinpile_draft, uid, conversation_id, task_id)
    except mcp_targets.TargetNotReady as error:
        raise _not_ready(error)
    return CoinpileSendRequest(
        kind='note', conversation_id=conversation_id, task_id=task_id, title=draft['title'], body=draft['body']
    )


@router.post('/v1/integrations/coinpile/send', tags=['integrations-outbound'], response_model=CoinpileSendResponse)
async def coinpile_send(request: CoinpileSendRequest, uid: str = Depends(auth.get_current_user_uid)):
    """Send a note or a transaction to Coinpile (the user's explicit OK). Coinpile only asks:
    nothing changes there until the user answers Yes on its Agent page."""
    try:
        answer = await mcp_targets.coinpile_send(uid, request.model_dump())
    except mcp_targets.TargetNotReady as error:
        return CoinpileSendResponse(ok=False, error=str(error))
    except OutboundError as error:
        return CoinpileSendResponse(ok=False, error=error.message)
    return CoinpileSendResponse(ok=True, answer=answer)
