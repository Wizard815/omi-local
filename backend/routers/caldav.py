"""CalDAV: connect the user's tasks and calendar to a CalDAV server (omi-host
bundles Radicale; Nextcloud, Baïkal, iCloud and the like work the same).
See utils/caldav/sync.py for what moves in which direction."""

from fastapi import APIRouter, Depends, HTTPException

import database.caldav_settings as settings_db
import database.day_bundle_users as active_users_db
from models.caldav import (
    CalDavCalendar,
    CalDavSettingsResponse,
    CalDavSyncResponse,
    CalDavTestResponse,
    UpdateCalDavSettingsRequest,
)
from utils.caldav import sync as caldav_sync
from utils.caldav.client import CalDavError
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter()


def _present(uid: str) -> CalDavSettingsResponse:
    stored = settings_db.get_settings(uid)
    if stored and stored.get('url'):
        return CalDavSettingsResponse(
            configured=True,
            bundled=False,
            url=stored.get('url') or '',
            username=stored.get('username') or '',
            has_password=bool(stored.get('password_enc')),
            calendar_href=stored.get('calendar_href') or '',
            tasks_href=stored.get('tasks_href') or '',
            enabled=bool(stored.get('enabled', True)),
            push_tasks=bool(stored.get('push_tasks')),
            pull_tasks=bool(stored.get('pull_tasks')),
            push_day_summaries=bool(stored.get('push_day_summaries')),
            push_conversation_events=bool(stored.get('push_conversation_events')),
            push_conversations=bool(stored.get('push_conversations')),
            pull_events=bool(stored.get('pull_events')),
            last_sync_at=stored.get('last_sync_at'),
            last_error=stored.get('last_error') or '',
            last_report=stored.get('last_report') or {},
        )
    default = caldav_sync.default_settings()
    if default:
        return CalDavSettingsResponse(
            configured=True,
            bundled=True,
            url=default['url'],
            username=default['username'],
            has_password=True,
            last_sync_at=(stored or {}).get('last_sync_at'),
            last_error=(stored or {}).get('last_error') or '',
            last_report=(stored or {}).get('last_report') or {},
        )
    return CalDavSettingsResponse(configured=False)


@router.get('/v1/caldav/settings', tags=['caldav'], response_model=CalDavSettingsResponse)
async def get_caldav_settings(uid: str = Depends(auth.get_current_user_uid)):
    return await run_blocking(db_executor, _present, uid)


@router.put('/v1/caldav/settings', tags=['caldav'], response_model=CalDavSettingsResponse)
async def update_caldav_settings(request: UpdateCalDavSettingsRequest, uid: str = Depends(auth.get_current_user_uid)):
    """Set or change the server and what to sync. The password is stored encrypted and never returned."""
    data = {k: v for k, v in request.model_dump().items() if v is not None and k != 'password'}
    if 'url' in data:
        url = str(data['url']).strip()
        if url and not url.startswith(('http://', 'https://')):
            raise HTTPException(status_code=422, detail='url must start with http:// or https://')
        data['url'] = url

    def _save() -> CalDavSettingsResponse:
        settings_db.set_settings(uid, data, password=request.password)
        active_users_db.mark_active(uid)
        return _present(uid)

    return await run_blocking(db_executor, _save)


@router.delete('/v1/caldav/settings', tags=['caldav'], response_model=CalDavSettingsResponse)
async def delete_caldav_settings(uid: str = Depends(auth.get_current_user_uid)):
    """Forget the user's server (the bundled one, if any, applies again)."""

    def _delete() -> CalDavSettingsResponse:
        settings_db.delete_settings(uid)
        return _present(uid)

    return await run_blocking(db_executor, _delete)


@router.post('/v1/caldav/test', tags=['caldav'], response_model=CalDavTestResponse)
async def test_caldav_connection(uid: str = Depends(auth.get_current_user_uid)):
    """Connect with the effective settings and list the calendars found."""

    def _test() -> CalDavTestResponse:
        settings = caldav_sync.effective_settings(uid)
        if not settings:
            return CalDavTestResponse(ok=False, error='No CalDAV server configured')
        try:
            calendars = caldav_sync.test_connection(settings)
        except CalDavError as error:
            return CalDavTestResponse(ok=False, error=str(error))
        return CalDavTestResponse(ok=True, calendars=[CalDavCalendar(**c) for c in calendars])

    return await run_blocking(db_executor, _test)


@router.post('/v1/caldav/sync', tags=['caldav'], response_model=CalDavSyncResponse)
async def sync_caldav_now(uid: str = Depends(auth.get_current_user_uid)):
    """Run the sync now (the scheduler also runs it every hour)."""

    def _sync() -> CalDavSyncResponse:
        outcome = caldav_sync.run_for_user(uid)
        if outcome is None:
            return CalDavSyncResponse(ok=False, error='No CalDAV server configured')
        if 'error' in outcome and len(outcome) == 1:
            return CalDavSyncResponse(ok=False, error=str(outcome['error']))
        return CalDavSyncResponse(ok=not outcome.get('errors'), **outcome)

    return await run_blocking(db_executor, _sync)
