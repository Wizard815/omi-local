"""End-of-day review: pure rules (no I/O).

The user sorts a day's conversations: the ones they star stay as normal
conversations; the rest are rolled into one long, chronological day summary
that the user can correct like a shared doc —

- ``paragraph``: a whole paragraph the user adds (inserted after a paragraph
  of the summary, or at the end);
- ``highlight``: a span of the summary with the user's correction/comment
  attached (like a Google Docs comment);
- ``redaction``: a span that must never appear — masked in the summary, in
  everything fed to the model, and in every regenerated version.

"Apply corrections" regenerates the summary with all corrections as ground
truth; paragraphs and highlights are then marked applied (the model folded
them in), while redactions stay enforced forever.

Offsets (``start``/``end``) index the ``summary_text`` they were made on.
Only *unapplied* corrections are drawn over the current text; a regeneration
replaces the text, so stale offsets are never used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

REDACTED = '[redacted]'
CORRECTION_TYPES = ('paragraph', 'highlight', 'redaction')

# Local models often run with 8k-32k contexts. ~28k chars ≈ 7k tokens of day
# material leaves room for the instructions, corrections and a long answer.
DEFAULT_MAX_INPUT_CHARS = 28_000
_OVERVIEW_CAP = 1_200


def split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r'\n\s*\n', text or '') if p.strip()]


def join_paragraphs(paragraphs: Iterable[str]) -> str:
    return '\n\n'.join(p for p in paragraphs if p)


def redaction_quotes(corrections: Sequence[Mapping[str, Any]]) -> List[str]:
    quotes = {str(c.get('quote') or '').strip() for c in corrections if c.get('type') == 'redaction'}
    # Longest first so "Sam's address" is masked before "Sam".
    return sorted((q for q in quotes if q), key=len, reverse=True)


def apply_redactions(text: str, quotes: Sequence[str]) -> str:
    """Mask every occurrence of each quote, case-insensitively."""
    for quote in quotes:
        if quote:
            text = re.sub(re.escape(quote), REDACTED, text, flags=re.IGNORECASE)
    return text


def _nearest_occurrence(text: str, quote: str, hint: Optional[int]) -> int:
    """Index of the occurrence of ``quote`` closest to ``hint`` (the offset it was selected at), or -1."""
    positions = [m.start() for m in re.finditer(re.escape(quote), text)]
    if not positions:
        return -1
    if hint is None:
        return positions[0]
    return min(positions, key=lambda p: abs(p - hint))


def compose_final_text(summary_text: str, corrections: Sequence[Mapping[str, Any]]) -> str:
    """The reader-facing day summary: generated text + the user's pending corrections.

    Redactions are masked everywhere (including user-added paragraphs).
    Unapplied highlight comments appear inline as "[correction: ...]" right
    after the span they were attached to; unapplied paragraphs are inserted
    after their anchor paragraph (or at the end), in the order they were added.
    """
    quotes = redaction_quotes(corrections)
    text = apply_redactions(summary_text or '', quotes)

    pending = [c for c in corrections if not c.get('applied_at')]
    inserts: List[Tuple[int, str]] = []
    for c in pending:
        if c.get('type') != 'highlight':
            continue
        note = str(c.get('text') or '').strip()
        quote = str(c.get('quote') or '')
        if not note or not quote:
            continue
        at = _nearest_occurrence(text, quote, c.get('start'))
        if at >= 0:
            inserts.append((at + len(quote), f' [correction: {apply_redactions(note, quotes)}]'))
    for position, insert in sorted(inserts, key=lambda pair: pair[0], reverse=True):
        text = text[:position] + insert + text[position:]

    paragraphs = split_paragraphs(text)
    added = [c for c in pending if c.get('type') == 'paragraph' and str(c.get('text') or '').strip()]
    tail: List[str] = []
    by_anchor: dict[int, List[str]] = {}
    for c in added:
        body = apply_redactions(str(c['text']).strip(), quotes)
        anchor = c.get('after_paragraph')
        if isinstance(anchor, int) and 0 <= anchor < len(paragraphs):
            by_anchor.setdefault(anchor, []).append(body)
        else:
            tail.append(body)
    out: List[str] = []
    for i, paragraph in enumerate(paragraphs):
        out.append(paragraph)
        out.extend(by_anchor.get(i, []))
    out.extend(tail)
    return join_paragraphs(out)


@dataclass(frozen=True)
class MaterialItem:
    """One rolled-up conversation, reduced to what the summary prompt needs."""

    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    title: str
    overview: str
    action_items: List[str]
    transcript: str


def _time_label(started: Optional[datetime], finished: Optional[datetime], tz: Any) -> str:
    def fmt(dt: Optional[datetime]) -> str:
        if dt is None:
            return '?'
        if tz is not None and dt.tzinfo is not None:
            dt = dt.astimezone(tz)
        return dt.strftime('%H:%M')

    return f'{fmt(started)}–{fmt(finished)}'


def render_material(
    items: Sequence[MaterialItem],
    *,
    redactions: Sequence[str] = (),
    max_chars: int = DEFAULT_MAX_INPUT_CHARS,
    tz: Any = None,
) -> str:
    """Chronological day material within ``max_chars``.

    Every conversation keeps its header and overview (the processed summary
    the pipeline already wrote); whatever budget is left is shared evenly as
    transcript excerpts, so one three-hour meeting cannot crowd out the rest of
    the day. Redacted text is masked before the model ever sees it.
    """
    ordered = sorted(items, key=lambda it: (it.started_at is None, it.started_at or datetime.min))
    heads: List[str] = []
    for it in ordered:
        lines = [f'### {_time_label(it.started_at, it.finished_at, tz)} — {it.title or "Untitled conversation"}']
        if it.overview:
            lines.append(it.overview.strip()[:_OVERVIEW_CAP])
        if it.action_items:
            lines.append('Action items: ' + '; '.join(a.strip() for a in it.action_items[:8] if a.strip()))
        heads.append('\n'.join(lines))

    used = sum(len(h) for h in heads) + 4 * len(heads)
    remaining = max(0, max_chars - used)
    with_transcript = [it for it in ordered if it.transcript.strip()]
    share = remaining // len(with_transcript) if with_transcript else 0

    blocks: List[str] = []
    for head, it in zip(heads, ordered):
        block = head
        transcript = it.transcript.strip()
        if transcript and share >= 200:
            excerpt = transcript if len(transcript) <= share else transcript[:share].rsplit(' ', 1)[0] + ' …'
            block += '\nTranscript excerpt:\n' + excerpt
        blocks.append(block)
    return apply_redactions('\n\n'.join(blocks), redactions)[: max_chars + 2_000]


def build_prompt(
    *,
    date_label: str,
    material: str,
    corrections: Sequence[Mapping[str, Any]],
    previous_summary: Optional[str] = None,
) -> str:
    quotes = redaction_quotes(corrections)
    fact_lines: List[str] = []
    for c in corrections:
        kind = c.get('type')
        if kind == 'paragraph' and str(c.get('text') or '').strip():
            fact_lines.append(
                f'- Include this (written by the user): {apply_redactions(str(c["text"]).strip(), quotes)}'
            )
        elif kind == 'highlight' and str(c.get('text') or '').strip():
            quote = apply_redactions(str(c.get('quote') or '').strip(), quotes)
            fact_lines.append(f'- About "{quote}": {apply_redactions(str(c["text"]).strip(), quotes)}')
    never_lines = [f'- "{q}"' for q in quotes]

    sections = [
        f'You are writing the user\'s personal end-of-day summary for {date_label}.',
        'Below are the conversations of that day that the user chose to fold into one summary '
        '(their highlights were kept separately). Write it in second person ("you").',
        '',
        'Rules:',
        '- Write a long, chronological narrative: what happened, with whom, what was decided, what is left to do.',
        '- Plain text only: no markdown, no bullet symbols, no headings. Separate paragraphs with one blank line.',
        '- 4 to 12 paragraphs depending on how much happened. Do not invent details that are not in the material.',
        '- Start with one line "TITLE: <a short title for the day>", then a blank line, then the summary.',
    ]
    if fact_lines:
        sections += [
            '',
            'The user reviewed an earlier draft and corrected it. These corrections are ground truth and '
            'override the material when they disagree:',
            *fact_lines,
        ]
    if never_lines:
        sections += [
            '',
            'The user redacted the following. Never mention them, hint at them, or paraphrase them:',
            *never_lines,
        ]
    if previous_summary:
        sections += [
            '',
            'Earlier draft (rewrite it, applying the corrections; keep what is still accurate):',
            apply_redactions(previous_summary, quotes),
        ]
    sections += ['', 'Conversations:', apply_redactions(material, quotes) or '(no conversation content)']
    return '\n'.join(sections)


_MARKDOWN_NOISE = re.compile(r'^\s{0,3}(#{1,6}\s+|[-*•]\s+|\d+\.\s+)', re.MULTILINE)


def parse_summary_output(raw: str, *, redactions: Sequence[str] = ()) -> Tuple[str, str]:
    """(title, body) from the model's answer, with redactions enforced on both."""
    text = (raw or '').strip()
    text = re.sub(r'^```[a-zA-Z]*\n?|\n?```$', '', text).strip()
    text = text.replace('**', '').replace('__', '')
    title = ''
    match = re.match(r'^\s*TITLE:\s*(.+?)\s*(?:\n|$)', text, flags=re.IGNORECASE)
    if match:
        title = match.group(1).strip().strip('"')
        text = text[match.end() :].strip()
    text = _MARKDOWN_NOISE.sub('', text)
    body = join_paragraphs(split_paragraphs(text))
    if not title:
        first = split_paragraphs(body)[0] if body else ''
        title = (first.split('. ')[0][:80]).strip() or 'Your day'
    return apply_redactions(title, redactions), apply_redactions(body, redactions)
