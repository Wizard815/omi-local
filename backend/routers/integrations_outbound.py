"""Connections (omi-local): where Omi may send the user's things, and the explicit sends.

GET/PUT /v1/integrations/outbound hold the user's targets (wiki, Hermes, Odysseus, Studio,
Coinpile, Pondering Orb); tokens are write-only. POST .../{target}/test is the functional
probe behind "Test connection". What leaves and when: utils/integrations_out.py.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Path

import database.integrations_out as outbound_db
from database.redis_db import get_user_webhook_db
from models.users import WebhookType
from models.integrations_out import (
    OutboundIntegrationsResponse,
    OutboundTestResponse,
    UpdateOutboundIntegrationsRequest,
)
from utils import integrations_out
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
