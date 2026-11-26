"""Family calendar (omi-local): review the events Omi heard before they go to dayGLANCE
through the ALLGlance gateway. See utils/family_calendar.py for the why and the where."""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from models.family_calendar import (
    ApproveFamilyCalendarEventRequest,
    DeclineFamilyCalendarEventRequest,
    FamilyDayResponse,
    FamilyCalendarEventResponse,
    FamilyCalendarMember,
    FamilyCalendarResponse,
    SetFamilyCalendarOwnerRequest,
)
from utils import family_calendar
from utils.caldav import sync as caldav_sync
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter()


async def _present(uid: str, conversation_id: Optional[str] = None) -> FamilyCalendarResponse:
    def _read():
        target = family_calendar.integration(uid)
        waiting, recent = family_calendar.pending(uid, conversation_id=conversation_id)
        return target, waiting, recent, caldav_sync.effective_settings(uid) is not None

    target, waiting, recent, caldav = await run_blocking(db_executor, _read)
    if target is None:
        return FamilyCalendarResponse(configured=False, caldav=caldav, pending=waiting, recent=recent)
    roster = await family_calendar.members(target)
    owner_id = target.get('owner_id')
    owner_name = next((m['name'] for m in roster if m['id'] == owner_id), owner_id or '')
    return FamilyCalendarResponse(
        configured=True,
        caldav=caldav,
        label=family_calendar.LABEL,
        members=[FamilyCalendarMember(**m) for m in roster],
        owner_id=owner_id,
        owner_name=owner_name or '',
        pending=waiting,
        recent=recent,
    )


@router.get('/v1/family-calendar', tags=['family-calendar'], response_model=FamilyCalendarResponse)
async def get_family_calendar(
    conversation_id: Optional[str] = Query(
        default=None, max_length=128, description='Only the events heard in this conversation, whatever their age'
    ),
    uid: str = Depends(auth.get_current_user_uid),
):
    """Whether a family calendar is set, the household and the member Omi acts as, the events waiting
    for review, and the ones reviewed lately. With ``conversation_id``, the events of that conversation."""
    return await _present(uid, conversation_id)


@router.get('/v1/family-calendar/today', tags=['family-calendar'], response_model=FamilyDayResponse)
async def get_family_calendar_today(uid: str = Depends(auth.get_current_user_uid)):
    """Today on the family calendar, sized for a home-screen widget: the next blocks still ahead,
    open tasks due today and overdue chores, for the member Omi acts as (or the whole family).
    A gateway that does not answer gives ``reachable: false`` and an ``error`` to show, not a 5xx,
    so a widget can keep what it showed last."""
    return await family_calendar.today(uid)


@router.put('/v1/family-calendar/owner', tags=['family-calendar'], response_model=FamilyCalendarResponse)
async def set_family_calendar_owner(
    request: SetFamilyCalendarOwnerRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Which household member Omi adds events as (null = the whole family)."""
    await run_blocking(db_executor, family_calendar.set_owner, uid, request.owner_id)
    return await _present(uid)


@router.post('/v1/family-calendar/events', tags=['family-calendar'], response_model=FamilyCalendarEventResponse)
async def approve_family_calendar_event(
    request: ApproveFamilyCalendarEventRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Approve one reviewed event, with the user's edits: onto the family calendar when there is one,
    and onto the user's CalDAV calendar at the next sync. The only path that approves one."""
    try:
        event = await family_calendar.approve(
            uid,
            request.conversation_id,
            request.event_idx,
            title=request.title,
            description=request.description,
            start=request.start,
            duration=request.duration,
        )
    except LookupError as error:
        return FamilyCalendarEventResponse(ok=False, error=str(error))
    except RuntimeError as error:
        return FamilyCalendarEventResponse(ok=False, error=str(error))
    except Exception as error:  # noqa: BLE001 — the gateway may be down; the app shows the text
        return FamilyCalendarEventResponse(ok=False, error=f'Could not reach the family calendar: {error}')
    return FamilyCalendarEventResponse(ok=True, event=event)


@router.post('/v1/family-calendar/events/decline', tags=['family-calendar'], response_model=FamilyCalendarEventResponse)
async def decline_family_calendar_event(
    request: DeclineFamilyCalendarEventRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Stop asking about one heard event, or (``declined: false``) bring a declined one back for review."""
    try:
        event = await run_blocking(
            db_executor, family_calendar.decline, uid, request.conversation_id, request.event_idx, request.declined
        )
    except LookupError as error:
        return FamilyCalendarEventResponse(ok=False, error=str(error))
    return FamilyCalendarEventResponse(ok=True, event=event)
