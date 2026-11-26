"""Home Assistant (omi-local): the user's connection and event choices, and the service calls
agents asked for, which wait here for the user's OK. See utils/home_assistant/."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

import database.day_bundle_users as active_users_db
import database.home_assistant as store
from models.home_assistant import (
    HomeAssistantAction,
    HomeAssistantActionResponse,
    HomeAssistantActionsResponse,
    HomeAssistantSettingsResponse,
    HomeAssistantTestResponse,
    UpdateHomeAssistantSettingsRequest,
)
from utils.executors import db_executor, run_blocking
from utils.home_assistant import actions, client
from utils.home_assistant import settings as ha_settings
from utils.other import endpoints as auth

router = APIRouter()


def _present(uid: str) -> HomeAssistantSettingsResponse:
    return HomeAssistantSettingsResponse.model_validate(ha_settings.present(ha_settings.effective(uid)))


@router.get('/v1/home-assistant/settings', tags=['home-assistant'], response_model=HomeAssistantSettingsResponse)
async def get_home_assistant_settings(uid: str = Depends(auth.get_current_user_uid)):
    return await run_blocking(db_executor, _present, uid)


@router.put('/v1/home-assistant/settings', tags=['home-assistant'], response_model=HomeAssistantSettingsResponse)
async def update_home_assistant_settings(
    request: UpdateHomeAssistantSettingsRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Own address/token (optional), which events to fire, and the name events carry. The token is
    stored encrypted and never returned."""
    data: Dict[str, Any] = request.model_dump(exclude_none=True, exclude={'token', 'events'})
    if request.url is not None:
        try:
            data['url'] = ha_settings.clean_url(request.url)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
    if request.events is not None:
        data['events'] = request.events.model_dump(exclude_none=True)

    def _save() -> HomeAssistantSettingsResponse:
        store.set_settings(uid, data, token=request.token)
        active_users_db.mark_active(uid)  # the event tick walks active members
        return _present(uid)

    return await run_blocking(db_executor, _save)


@router.delete('/v1/home-assistant/settings', tags=['home-assistant'], response_model=HomeAssistantSettingsResponse)
async def delete_home_assistant_settings(uid: str = Depends(auth.get_current_user_uid)):
    """Forget the user's own choices (the stack's connection, if any, applies again)."""

    def _delete() -> HomeAssistantSettingsResponse:
        store.delete_settings(uid)
        return _present(uid)

    return await run_blocking(db_executor, _delete)


@router.post('/v1/home-assistant/test', tags=['home-assistant'], response_model=HomeAssistantTestResponse)
async def test_home_assistant(uid: str = Depends(auth.get_current_user_uid)):
    """A functional probe with the effective connection: GET /api/ with the token."""
    settings = await run_blocking(db_executor, ha_settings.effective, uid)
    try:
        message = await client.ping(settings)
    except client.HomeAssistantError as error:
        await run_blocking(db_executor, lambda: store.record_check(uid, ok=False, error=error.message))
        return HomeAssistantTestResponse(ok=False, error=error.message)
    await run_blocking(db_executor, lambda: store.record_check(uid, ok=True))
    return HomeAssistantTestResponse(ok=True, detail=message[:200])


@router.get('/v1/home-assistant/actions', tags=['home-assistant'], response_model=HomeAssistantActionsResponse)
async def list_home_assistant_actions(uid: str = Depends(auth.get_current_user_uid)):
    """The action log, newest first: what agents asked for and what happened."""
    rows = await run_blocking(db_executor, actions.recent, uid)
    return HomeAssistantActionsResponse(
        actions=[HomeAssistantAction.model_validate(r) for r in rows],
        waiting_count=sum(1 for r in rows if r.get('status') == actions.WAITING),
    )


async def _answer(uid: str, action_id: str, approve: bool) -> HomeAssistantActionResponse:
    try:
        row = await (actions.approve(uid, action_id) if approve else actions.decline(uid, action_id))
    except actions.ActionError as error:
        if error.status_code == 404:
            raise HTTPException(status_code=404, detail=error.message)
        return HomeAssistantActionResponse(ok=False, error=error.message)
    action = HomeAssistantAction.model_validate(row)
    return HomeAssistantActionResponse(ok=action.status != 'failed', action=action, error=action.error)


@router.post(
    '/v1/home-assistant/actions/{action_id}/approve',
    tags=['home-assistant'],
    response_model=HomeAssistantActionResponse,
)
async def approve_home_assistant_action(action_id: str, uid: str = Depends(auth.get_current_user_uid)):
    """The user's OK: Omi's rules are checked again, then the call goes to Home Assistant."""
    return await _answer(uid, action_id, True)


@router.post(
    '/v1/home-assistant/actions/{action_id}/decline',
    tags=['home-assistant'],
    response_model=HomeAssistantActionResponse,
)
async def decline_home_assistant_action(action_id: str, uid: str = Depends(auth.get_current_user_uid)):
    return await _answer(uid, action_id, False)
