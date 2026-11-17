"""CalDAV: connect the user's tasks and calendar to a CalDAV server (omi-host
bundles Radicale; Nextcloud, Baïkal, iCloud and the like work the same).
See utils/caldav/sync.py for what moves in which direction."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

import database.caldav_settings as settings_db
import database.day_bundle_users as active_users_db
from models.caldav import (
    CalDavCalendar,
    CalDavCollection,
    CalDavCollectionResponse,
    CalDavCollectionsResponse,
    CalDavSettingsResponse,
    CreateCalDavCollectionRequest,
    CalDavSyncResponse,
    CalDavTestResponse,
    UpdateCalDavSettingsRequest,
)
from utils.caldav import sync as caldav_sync
from utils.caldav.client import CalDavError
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter()

# "Sync now" waits this long for a scheduled pass of the same user to finish.
SYNC_NOW_WAIT_SECONDS = 60.0


def _choices(stored: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'calendar_href': stored.get('calendar_href') or '',
        'tasks_href': stored.get('tasks_href') or '',
        'enabled': bool(stored.get('enabled', True)),
        'push_tasks': bool(stored.get('push_tasks', True)),
        'pull_tasks': bool(stored.get('pull_tasks', True)),
        'push_day_summaries': bool(stored.get('push_day_summaries', True)),
        'push_conversation_events': bool(stored.get('push_conversation_events', True)),
        'push_conversations': bool(stored.get('push_conversations', False)),
        'pull_events': bool(stored.get('pull_events', True)),
        'task_mode': stored.get('task_mode') if stored.get('task_mode') in caldav_sync.MODES else 'list',
        'last_sync_at': stored.get('last_sync_at'),
        'last_error': stored.get('last_error') or '',
        'last_report': stored.get('last_report') or {},
    }


def _present(uid: str) -> CalDavSettingsResponse:
    stored = settings_db.get_settings(uid)
    label = caldav_sync.owner_label(uid)
    calendar_name, tasks_name = caldav_sync.personal_names(label)
    names: Dict[str, Any] = {
        'owner_label': label,
        'suggested_calendar_name': calendar_name,
        'suggested_tasks_name': tasks_name,
        'parent_task_name': caldav_sync.parent_summary(label),
    }
    if stored and stored.get('url'):
        return CalDavSettingsResponse.model_validate(
            {
                'configured': True,
                'bundled': False,
                'url': stored.get('url') or '',
                'username': stored.get('username') or '',
                'has_password': bool(stored.get('password_enc')),
                **_choices(stored),
                **names,
            }
        )
    default = caldav_sync.default_settings()
    if default:
        # The stack's own server: one CalDAV login for every Omi user here; the user's own
        # choices (collections, mode, what to sync) still apply.
        return CalDavSettingsResponse.model_validate(
            {
                'configured': True,
                'bundled': True,
                'shared_account': True,
                'url': default['url'],
                'username': default['username'],
                'has_password': True,
                **_choices({**default, **(stored or {})}),
                **names,
            }
        )
    return CalDavSettingsResponse.model_validate({'configured': False, **names})


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
        # A new calendar, task list or mode reaches the server within seconds. The sync starts
        # its memos over for new collections, so nothing missing there reads as deleted.
        caldav_sync.mark_dirty(uid)
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
        outcome = caldav_sync.run_for_user(uid, wait=SYNC_NOW_WAIT_SECONDS)
        if outcome is None:
            return CalDavSyncResponse(ok=False, error='No CalDAV server configured')
        if 'error' in outcome and 'errors' not in outcome:  # failed or busy: no report
            return CalDavSyncResponse(ok=False, error=str(outcome['error']))
        return CalDavSyncResponse(ok=not outcome.get('errors'), **outcome)

    return await run_blocking(db_executor, _sync)


@router.get('/v1/caldav/collections', tags=['caldav'], response_model=CalDavCollectionsResponse)
async def list_caldav_collections(uid: str = Depends(auth.get_current_user_uid)):
    """The server's calendars and task lists, marking the user's own and the ones other people on this
    Omi server sync into (one CalDAV login is shared by everyone on the bundled server)."""

    def _list() -> CalDavCollectionsResponse:
        settings = caldav_sync.effective_settings(uid)
        if not settings:
            return CalDavCollectionsResponse(ok=False, error='No CalDAV server configured')
        try:
            found = caldav_sync.list_collections(uid, settings)
        except CalDavError as error:
            return CalDavCollectionsResponse(ok=False, error=str(error))
        return CalDavCollectionsResponse(ok=True, collections=[CalDavCollection(**c) for c in found])

    return await run_blocking(db_executor, _list)


@router.post('/v1/caldav/collections', tags=['caldav'], response_model=CalDavCollectionResponse)
async def create_caldav_collection(
    request: CreateCalDavCollectionRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Make a new calendar or task list on the server and (by default) make it the user's own."""

    def _create() -> CalDavCollectionResponse:
        settings = caldav_sync.effective_settings(uid)
        if not settings:
            return CalDavCollectionResponse(ok=False, error='No CalDAV server configured')
        try:
            made = caldav_sync.create_collection(uid, settings, request.name, request.kind)
        except CalDavError as error:
            return CalDavCollectionResponse(ok=False, error=str(error))
        if request.select:
            field = 'calendar_href' if request.kind == 'calendar' else 'tasks_href'
            settings_db.set_settings(uid, {field: made['href']})
            caldav_sync.mark_dirty(uid)
            made['mine'] = sorted({*made.get('mine', []), 'calendar' if request.kind == 'calendar' else 'tasks'})
        return CalDavCollectionResponse(ok=True, collection=CalDavCollection(**made), settings=_present(uid))

    return await run_blocking(db_executor, _create)
