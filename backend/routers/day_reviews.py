"""End-of-day review: sort a day's conversations, roll the unstarred ones into
one long day summary, and correct it (added paragraphs, highlights, redactions) or edit its
text by hand.

See utils/day_review.py (rules) and utils/day_review_service.py (side effects).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query

from models.day_review import (
    CreateDayReviewCorrectionRequest,
    DayReview,
    DayReviewDayResponse,
    DayReviewsListResponse,
    EditDayReviewTextRequest,
    FavoriteDayReviewSectionRequest,
    FinalizeDayReviewRequest,
    PendingDayReviewsResponse,
    ReopenDayReviewResponse,
)
from utils import day_review_service as service
from utils.other import endpoints as auth
from utils.subscription import enforce_chat_quota

router = APIRouter()

_DATE = Path(..., pattern=r'^\d{4}-\d{2}-\d{2}$', description='Local calendar date, YYYY-MM-DD')
_REPLACE_EDITS = Query(
    False,
    description='The user confirmed that a rewrite may replace the text they edited by hand '
    '(without it, such a rewrite answers 409)',
)


def _raise(error: service.DayReviewError):
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get('/v1/users/day-reviews/pending', tags=['day-reviews'], response_model=PendingDayReviewsResponse)
def get_pending_day_reviews(days: int = Query(7, ge=1, le=31), uid: str = Depends(auth.get_current_user_uid)):
    """Recent days that still have unsorted conversations (today included; the app decides when to nudge)."""
    return service.pending_dates(uid, days=days)


@router.get('/v1/users/day-reviews', tags=['day-reviews'], response_model=DayReviewsListResponse)
def list_day_reviews(
    limit: int = Query(30, ge=1, le=100),
    include_drafts: bool = Query(False, description='Also list summary drafts waiting for approval (`draft: true`)'),
    uid: str = Depends(auth.get_current_user_uid),
):
    return {'reviews': service.list_reviews(uid, limit=limit, include_drafts=include_drafts)}


@router.get('/v1/users/day-reviews/{date}', tags=['day-reviews'], response_model=DayReviewDayResponse)
def get_day_review(
    date: str = _DATE,
    include_drafts: bool = Query(
        False,
        description='Return a summary draft as the review (`draft: true`); without it a draft day reads as unsorted',
    ),
    uid: str = Depends(auth.get_current_user_uid),
):
    """The day's conversations (to sort) and its review, if it has one."""
    try:
        return service.get_day(uid, date, include_drafts=include_drafts)
    except service.DayReviewError as e:
        _raise(e)


@router.post('/v1/users/day-reviews/{date}/finalize', tags=['day-reviews'], response_model=DayReview)
def finalize_day_review(
    request: FinalizeDayReviewRequest,
    date: str = _DATE,
    replace_edits: bool = _REPLACE_EDITS,
    uid: str = Depends(auth.get_current_user_uid),
    x_app_platform: Optional[str] = Header(None, alias='X-App-Platform'),
):
    """Keep (star) the listed conversations, merge each group into one, drop the noise; roll every other one into one day summary."""
    enforce_chat_quota(uid, platform=x_app_platform)
    try:
        return service.finalize(
            uid,
            date,
            request.keep_conversation_ids,
            [g.conversation_ids for g in request.merge_groups],
            request.drop_conversation_ids,
            replace_edits=replace_edits,
        )
    except service.DayReviewError as e:
        _raise(e)


@router.post('/v1/users/day-reviews/{date}/bundle', tags=['day-reviews'], response_model=DayReview)
def bundle_day(
    date: str = _DATE,
    uid: str = Depends(auth.get_current_user_uid),
    x_app_platform: Optional[str] = Header(None, alias='X-App-Platform'),
):
    """Hands-off review as a draft: starred conversations would stay, every other one is written into one
    sectioned day summary. Nothing is sorted or sent to a calendar until the day is approved."""
    enforce_chat_quota(uid, platform=x_app_platform)
    try:
        return service.bundle(uid, date)
    except service.DayReviewError as e:
        _raise(e)


@router.post('/v1/users/day-reviews/{date}/approve', tags=['day-reviews'], response_model=DayReview)
def approve_day_review(
    date: str = _DATE,
    replace_edits: bool = _REPLACE_EDITS,
    uid: str = Depends(auth.get_current_user_uid),
    x_app_platform: Optional[str] = Header(None, alias='X-App-Platform'),
):
    """Approve the day's draft: finalize with its split and text (no new model call unless the day changed).
    Only an approved day's summary goes to the user's calendar."""
    enforce_chat_quota(uid, platform=x_app_platform)
    try:
        return service.approve(uid, date, replace_edits=replace_edits)
    except service.DayReviewError as e:
        _raise(e)


@router.post(
    '/v1/users/day-reviews/{date}/sections/{section_id}/favorite', tags=['day-reviews'], response_model=DayReview
)
def favorite_day_review_section(
    section_id: str,
    request: FavoriteDayReviewSectionRequest,
    date: str = _DATE,
    uid: str = Depends(auth.get_current_user_uid),
):
    """Pull a section's conversations back out of the bundle as starred conversations (favorited=false folds them back)."""
    try:
        return service.favorite_section(uid, date, section_id, request.favorited)
    except service.DayReviewError as e:
        _raise(e)


@router.post('/v1/users/day-reviews/{date}/corrections', tags=['day-reviews'], response_model=DayReview)
def add_day_review_correction(
    request: CreateDayReviewCorrectionRequest, date: str = _DATE, uid: str = Depends(auth.get_current_user_uid)
):
    try:
        return service.add_correction(uid, date, request.model_dump())
    except service.DayReviewError as e:
        _raise(e)


@router.delete(
    '/v1/users/day-reviews/{date}/corrections/{correction_id}', tags=['day-reviews'], response_model=DayReview
)
def delete_day_review_correction(correction_id: str, date: str = _DATE, uid: str = Depends(auth.get_current_user_uid)):
    try:
        return service.delete_correction(uid, date, correction_id)
    except service.DayReviewError as e:
        _raise(e)


@router.post('/v1/users/day-reviews/{date}/regenerate', tags=['day-reviews'], response_model=DayReview)
def regenerate_day_review(
    date: str = _DATE,
    replace_edits: bool = _REPLACE_EDITS,
    uid: str = Depends(auth.get_current_user_uid),
    x_app_platform: Optional[str] = Header(None, alias='X-App-Platform'),
):
    """Rewrite the summary with every correction applied (redactions stay enforced). Text the user
    edited by hand is replaced only with ``replace_edits=true``; otherwise 409."""
    enforce_chat_quota(uid, platform=x_app_platform)
    try:
        return service.regenerate(uid, date, replace_edits=replace_edits)
    except service.DayReviewError as e:
        _raise(e)


@router.put('/v1/users/day-reviews/{date}/text', tags=['day-reviews'], response_model=DayReview)
def edit_day_review_text(
    request: EditDayReviewTextRequest, date: str = _DATE, uid: str = Depends(auth.get_current_user_uid)
):
    """Edit the summary's text by hand: the title, and each section's heading and text. A draft stays
    a draft; an approved day stays approved and its summary on the calendar follows."""
    try:
        return service.edit_text(
            uid,
            date,
            title=request.title,
            sections=[s.model_dump() for s in request.sections] if request.sections is not None else None,
            summary_text=request.summary_text,
        )
    except service.DayReviewError as e:
        _raise(e)


@router.post('/v1/users/day-reviews/{date}/reopen', tags=['day-reviews'], response_model=ReopenDayReviewResponse)
def reopen_day_review(date: str = _DATE, uid: str = Depends(auth.get_current_user_uid)):
    """Undo the approval: rolled-up and dropped conversations come back and the summary becomes a draft again
    (off the calendar until re-approved). Reopening a draft discards it. Merges stay."""
    try:
        restored, merged = service.reopen(uid, date)
        return {'status': 'ok', 'restored_count': restored, 'merged_count': merged}
    except service.DayReviewError as e:
        _raise(e)
