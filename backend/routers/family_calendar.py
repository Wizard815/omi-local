"""Family calendar (omi-local): review the events Omi heard before they go to dayGLANCE
through the ALLGlance gateway. See utils/family_calendar.py for the why and the where."""

from fastapi import APIRouter, Depends

from models.family_calendar import (
    ApproveFamilyCalendarEventRequest,
    DeclineFamilyCalendarEventRequest,
    FamilyCalendarEventResponse,
    FamilyCalendarResponse,
)
from utils import family_calendar
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter()


@router.get('/v1/family-calendar', tags=['family-calendar'], response_model=FamilyCalendarResponse)
async def get_family_calendar(uid: str = Depends(auth.get_current_user_uid)):
    """Whether a family calendar is set, the events waiting for review, and the ones reviewed lately."""

    def _read() -> FamilyCalendarResponse:
        configured = family_calendar.integration(uid) is not None
        waiting, recent = family_calendar.pending(uid)
        return FamilyCalendarResponse(
            configured=configured, label=family_calendar.LABEL if configured else '', pending=waiting, recent=recent
        )

    return await run_blocking(db_executor, _read)


@router.post('/v1/family-calendar/events', tags=['family-calendar'], response_model=FamilyCalendarEventResponse)
async def approve_family_calendar_event(
    request: ApproveFamilyCalendarEventRequest, uid: str = Depends(auth.get_current_user_uid)
):
    """Put one reviewed event on the family calendar, with the user's edits. The only path that writes one."""
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
    try:
        event = await run_blocking(
            db_executor, family_calendar.decline, uid, request.conversation_id, request.event_idx
        )
    except LookupError as error:
        return FamilyCalendarEventResponse(ok=False, error=str(error))
    return FamilyCalendarEventResponse(ok=True, event=event)
