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


class DayReviewMergeGroup(BaseModel):
    conversation_ids: List[str] = Field(default_factory=list, min_length=2, max_length=200)


class DayReviewSection(BaseModel):
    id: str
    conversation_ids: List[str] = Field(
        default_factory=list, description='The rolled-up conversations this part covers'
    )
    title: str = ''
    text: str = ''
    tags: List[str] = Field(default_factory=list)
    favorited: bool = Field(
        default=False, description='Its conversations were pulled back out as starred conversations'
    )


class DayReview(BaseModel):
    date: str
    status: Literal['finalized'] = 'finalized'
    title: str = ''
    summary_text: str = Field(default='', description='Model-written summary; correction offsets index this')
    final_text: str = Field(
        default='', description='summary_text with redactions masked and pending corrections applied'
    )
    sections: List[DayReviewSection] = Field(
        default_factory=list, description='summary_text, split by source conversations'
    )
    tags: List[str] = Field(default_factory=list, description='Every section tag of the day')
    corrections: List[DayReviewCorrection] = Field(default_factory=list)
    kept_conversation_ids: List[str] = Field(default_factory=list)
    rolled_up_conversation_ids: List[str] = Field(default_factory=list)
    merged_groups: List[DayReviewMergeGroup] = Field(
        default_factory=list, description='Each group was merged into one new conversation (sources deleted)'
    )
    dropped_conversation_ids: List[str] = Field(default_factory=list, description='Dropped as noise (hidden)')
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
    tags: List[str] = Field(default_factory=list)
    rolled_up: bool = Field(default=False, description='Folded into this day\'s summary (hidden from the normal list)')
    dropped: bool = Field(default=False, description='Dropped as noise by this review (hidden)')
    held: bool = Field(default=False, description='Transcript only so far; the review decides whether it is processed')
    hold_rule: str = Field(default='', description='Noise rule that let a held clip through, e.g. model_withheld')
    transcript_snippet: str = ''
    word_count: int = 0


class DayReviewDayResponse(BaseModel):
    date: str
    review: Optional[DayReview] = None
    conversations: List[DayReviewConversation] = Field(default_factory=list)
    processing_count: int = Field(default=0, description='Conversations of this day still being processed')
    dropped_auto_count: int = Field(default=0, description='Clips the noise rules dropped on their own')
    hold_enabled: bool = Field(default=False, description='Finished clips wait for this review (no model calls)')


class FinalizeDayReviewRequest(BaseModel):
    keep_conversation_ids: List[str] = Field(
        default_factory=list, max_length=500, description='Starred: stay normal conversations. The rest are rolled up.'
    )
    merge_groups: List[DayReviewMergeGroup] = Field(
        default_factory=list, max_length=100, description='Groups of 2+ clips, each merged into one kept conversation'
    )
    drop_conversation_ids: List[str] = Field(
        default_factory=list, max_length=500, description='Noise: hidden, not summarized'
    )

    @model_validator(mode='after')
    def _disjoint(self):
        seen: set = set()
        for group in self.merge_groups:
            if len(set(group.conversation_ids)) < 2:
                raise ValueError('A merge group needs at least 2 different conversations')
        for cid in [
            *self.keep_conversation_ids,
            *self.drop_conversation_ids,
            *(c for g in self.merge_groups for c in g.conversation_ids),
        ]:
            if cid in seen:
                raise ValueError(f'Conversation {cid} is listed more than once')
            seen.add(cid)
        return self


class FavoriteDayReviewSectionRequest(BaseModel):
    favorited: bool = True


class DayReviewsListResponse(BaseModel):
    reviews: List[DayReview] = Field(default_factory=list)


class PendingDayReviewsResponse(BaseModel):
    today: str
    pending_dates: List[str] = Field(default_factory=list, description='Recent local dates with unsorted conversations')


class ReopenDayReviewResponse(BaseModel):
    status: str = 'ok'
    restored_count: int = 0
    merged_count: int = Field(default=0, description='Merged conversations stay merged; their sources are gone')
