/// Splits a day summary into paragraphs and marks the user's pending
/// highlights/redactions on it, keeping exact offsets into the summary text so
/// a selection inside a paragraph maps back to the offsets the backend expects.
library;

import 'package:omi/backend/schema/day_review.dart';

enum SummarySegmentKind { plain, highlight, redaction }

class SummarySegment {
  final String text;
  final SummarySegmentKind kind;
  final String? correctionId;

  const SummarySegment(this.text, this.kind, {this.correctionId});
}

class SummaryParagraph {
  final int index;

  /// Offset of the paragraph's first character in the summary text.
  final int start;
  final String text;
  final List<SummarySegment> segments;

  const SummaryParagraph({required this.index, required this.start, required this.text, required this.segments});

  int get end => start + text.length;
}

class _Mark {
  final int start;
  final int end;
  final SummarySegmentKind kind;
  final String id;

  const _Mark(this.start, this.end, this.kind, this.id);
}

/// Where a correction sits in [text] now: its recorded offsets if they still
/// hold the quote, else the occurrence nearest to them; null when gone (e.g. a
/// regenerated summary no longer contains it).
({int start, int end})? locateCorrection(String text, DayReviewCorrection c) {
  final quote = c.quote ?? '';
  if (quote.isEmpty) return null;
  final s = c.start, e = c.end;
  if (s != null && e != null && s >= 0 && e <= text.length && s < e && text.substring(s, e) == quote) {
    return (start: s, end: e);
  }
  int? best;
  var from = 0;
  while (true) {
    final at = text.indexOf(quote, from);
    if (at < 0) break;
    if (best == null || (s != null && (at - s).abs() < (best - s).abs())) best = at;
    if (s == null) break;
    from = at + 1;
  }
  return best == null ? null : (start: best, end: best + quote.length);
}

List<SummaryParagraph> buildSummaryParagraphs(String text, List<DayReviewCorrection> corrections) {
  final marks = <_Mark>[];
  for (final c in corrections) {
    if (c.isApplied || c.type == DayReviewCorrectionType.paragraph) continue;
    final at = locateCorrection(text, c);
    if (at == null) continue;
    marks.add(_Mark(
      at.start,
      at.end,
      c.type == DayReviewCorrectionType.redaction ? SummarySegmentKind.redaction : SummarySegmentKind.highlight,
      c.id,
    ));
  }

  final paragraphs = <SummaryParagraph>[];
  var cursor = 0;
  var index = 0;
  while (cursor <= text.length) {
    final next = text.indexOf('\n\n', cursor);
    final end = next < 0 ? text.length : next;
    final body = text.substring(cursor, end);
    if (body.trim().isNotEmpty) {
      paragraphs.add(SummaryParagraph(
        index: index,
        start: cursor,
        text: body,
        segments: _segments(body, cursor, marks),
      ));
      index++;
    }
    if (next < 0) break;
    cursor = next + 2;
  }
  return paragraphs;
}

List<SummarySegment> _segments(String body, int offset, List<_Mark> marks) {
  // Kind per character: a redaction wins over a highlight on overlap.
  final kinds = List<SummarySegmentKind>.filled(body.length, SummarySegmentKind.plain);
  final ids = List<String?>.filled(body.length, null);
  for (final kind in [SummarySegmentKind.highlight, SummarySegmentKind.redaction]) {
    for (final m in marks.where((m) => m.kind == kind)) {
      final from = (m.start - offset).clamp(0, body.length);
      final to = (m.end - offset).clamp(0, body.length);
      for (var i = from; i < to; i++) {
        kinds[i] = kind;
        ids[i] = m.id;
      }
    }
  }
  final out = <SummarySegment>[];
  var runStart = 0;
  for (var i = 1; i <= body.length; i++) {
    if (i == body.length || kinds[i] != kinds[runStart] || ids[i] != ids[runStart]) {
      out.add(SummarySegment(body.substring(runStart, i), kinds[runStart], correctionId: ids[runStart]));
      runStart = i;
    }
  }
  return out;
}

/// The sections whose text begins at [paragraphStart] (an offset into the summary text).
///
/// The server writes summaryText as the sections' texts joined by one blank line, so every
/// section starts at a paragraph boundary; the page draws a section's header above that
/// paragraph. A section with empty text has no place in the text and is never returned.
List<DayReviewSection> sectionsStartingAt(List<DayReviewSection> sections, int paragraphStart) {
  final out = <DayReviewSection>[];
  var offset = 0;
  for (final section in sections) {
    if (section.text.isEmpty) continue;
    if (offset == paragraphStart) out.add(section);
    offset += section.text.length + 2;
  }
  return out;
}
