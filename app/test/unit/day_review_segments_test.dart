import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/utils/day_review/summary_segments.dart';

DayReviewCorrection _c(String id, DayReviewCorrectionType type, String quote, {int? start, DateTime? appliedAt}) =>
    DayReviewCorrection(
      id: id,
      type: type,
      quote: quote,
      start: start,
      end: start == null ? null : start + quote.length,
      appliedAt: appliedAt,
    );

void main() {
  const text = 'You met Sam at noon.\n\nLater Sam called about the trip.';

  test('paragraph offsets index the summary text exactly', () {
    final paragraphs = buildSummaryParagraphs(text, const []);
    expect(paragraphs.length, 2);
    for (final p in paragraphs) {
      expect(text.substring(p.start, p.end), p.text);
    }
    expect(paragraphs[1].start, text.indexOf('Later'));
  });

  test('marks land on the selected occurrence, redaction wins over highlight', () {
    final secondSam = text.lastIndexOf('Sam');
    final paragraphs = buildSummaryParagraphs(text, [
      _c('h', DayReviewCorrectionType.highlight, 'Sam called', start: secondSam),
      _c('r', DayReviewCorrectionType.redaction, 'Sam', start: secondSam),
    ]);
    expect(paragraphs[0].segments.single.kind, SummarySegmentKind.plain);
    final kinds = paragraphs[1].segments.map((s) => (s.text, s.kind)).toList();
    expect(kinds, [
      ('Later ', SummarySegmentKind.plain),
      ('Sam', SummarySegmentKind.redaction),
      (' called', SummarySegmentKind.highlight),
      (' about the trip.', SummarySegmentKind.plain),
    ]);
    // Segments still concatenate to the paragraph, so selections map 1:1.
    expect(paragraphs[1].segments.map((s) => s.text).join(), paragraphs[1].text);
  });

  test('stale offsets re-anchor by quote; applied or vanished corrections are not drawn', () {
    final moved = _c('h', DayReviewCorrectionType.highlight, 'the trip', start: 2);
    expect(locateCorrection(text, moved), (start: text.indexOf('the trip'), end: text.indexOf('the trip') + 8));
    final paragraphs = buildSummaryParagraphs(text, [
      _c('a', DayReviewCorrectionType.highlight, 'noon', appliedAt: DateTime(2026)),
      _c('g', DayReviewCorrectionType.redaction, 'not in the text anymore'),
    ]);
    expect(paragraphs.every((p) => p.segments.every((s) => s.kind == SummarySegmentKind.plain)), isTrue);
  });
}
