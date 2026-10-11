"""API shapes for the end-of-day review (see utils/day_review.py)."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

DayReviewCorrectionType = Literal['paragraph', 'highlight', 'redaction']


class DayReviewCorrection(BaseModel):
    id: str
    type: DayReviewCorrectionType
    quote: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    text: Optional[str] = None
    after_paragraph: Optional[int] = None
    created_at: datetime
    applied_at: Optional[datetime] = None


class CreateDayReviewCorrectionRequest(BaseModel):
    type: DayReviewCorrectionType
    quote: Optional[str] = Field(
        default=None, max_length=2000, description='Selected summary text (highlight/redaction)'
    )
    start: Optional[int] = Field(default=None, ge=0, description='Selection start offset in summary_text')
    end: Optional[int] = Field(default=None, ge=0, description='Selection end offset in summary_text')
    text: Optional[str] = Field(default=None, max_length=8000, description='Paragraph body, or the highlight comment')
    after_paragraph: Optional[int] = Field(
        default=None, ge=0, description='Insert a paragraph after this paragraph index (omit = at the end)'
    )

    @model_validator(mode='after')
    def _shape_matches_type(self):
        if self.type == 'paragraph' and not (self.text or '').strip():
            raise ValueError('A paragraph needs text')
        if self.type in ('highlight', 'redaction') and not (self.quote or '').strip():
            raise ValueError(f'A {self.type} needs the selected text (quote)')
        if self.type == 'highlight' and not (self.text or '').strip():
            raise ValueError('A highlight needs a correction or comment')
        return self


class DayReview(BaseModel):
    date: str
    status: Literal['finalized'] = 'finalized'
    title: str = ''
    summary_text: str = Field(default='', description='Model-written summary; correction offsets index this')
    final_text: str = Field(
        default='', description='summary_text with redactions masked and pending corrections applied'
    )
    corrections: List[DayReviewCorrection] = Field(default_factory=list)
    kept_conversation_ids: List[str] = Field(default_factory=list)
    rolled_up_conversation_ids: List[str] = Field(default_factory=list)
    time_zone: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    generation_count: int = 0


class DayReviewConversation(BaseModel):
    id: str
    title: str = ''
    emoji: str = ''
    overview: str = ''
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    starred: bool = False
    rolled_up: bool = Field(default=False, description='Folded into this day\'s summary (hidden from the normal list)')


class DayReviewDayResponse(BaseModel):
    date: str
    review: Optional[DayReview] = None
    conversations: List[DayReviewConversation] = Field(default_factory=list)
    processing_count: int = Field(default=0, description='Conversations of this day still being processed')


class FinalizeDayReviewRequest(BaseModel):
    keep_conversation_ids: List[str] = Field(
        default_factory=list, max_length=500, description='Starred: stay normal conversations. The rest are rolled up.'
    )


class DayReviewsListResponse(BaseModel):
    reviews: List[DayReview] = Field(default_factory=list)


class PendingDayReviewsResponse(BaseModel):
    today: str
    pending_dates: List[str] = Field(default_factory=list, description='Recent local dates with unsorted conversations')


class ReopenDayReviewResponse(BaseModel):
    status: str = 'ok'
    restored_count: int = 0
