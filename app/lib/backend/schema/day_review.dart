/// End-of-day review (backend: /v1/users/day-reviews). Domain models built
/// from the generated wire DTOs (schema/gen/self_hosted_wire.g.dart).
library;

import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;

enum DayReviewCorrectionType { paragraph, highlight, redaction }

class DayReviewCorrection {
  final String id;
  final DayReviewCorrectionType type;
  final String? quote;
  final int? start;
  final int? end;
  final String? text;
  final int? afterParagraph;
  final DateTime? createdAt;
  final DateTime? appliedAt;

  const DayReviewCorrection({
    required this.id,
    required this.type,
    this.quote,
    this.start,
    this.end,
    this.text,
    this.afterParagraph,
    this.createdAt,
    this.appliedAt,
  });

  bool get isApplied => appliedAt != null;

  factory DayReviewCorrection.fromGenerated(wire.GeneratedDayReviewCorrection g) => DayReviewCorrection(
        id: g.id,
        type: DayReviewCorrectionType.values.byName(g.type),
        quote: g.quote,
        start: g.start,
        end: g.end,
        text: g.text,
        afterParagraph: g.afterParagraph,
        createdAt: g.createdAt.toLocal(),
        appliedAt: g.appliedAt?.toLocal(),
      );
}

class DayReview {
  final String date;
  final String title;

  /// What the model wrote. Correction offsets index this text.
  final String summaryText;

  /// summaryText with redactions masked and pending corrections folded in.
  final String finalText;
  final List<DayReviewCorrection> corrections;
  final List<String> keptConversationIds;
  final List<String> rolledUpConversationIds;
  final DateTime? generatedAt;
  final int generationCount;

  const DayReview({
    required this.date,
    required this.title,
    required this.summaryText,
    required this.finalText,
    required this.corrections,
    required this.keptConversationIds,
    required this.rolledUpConversationIds,
    this.generatedAt,
    this.generationCount = 0,
  });

  /// Corrections the summary text does not reflect yet: unapplied paragraphs and
  /// highlights, plus redactions added since the last generation (until then
  /// they show as a bar instead of being written around).
  int get pendingCorrectionCount => corrections.where((c) {
        if (c.type == DayReviewCorrectionType.redaction) {
          return generatedAt == null || c.createdAt == null || c.createdAt!.isAfter(generatedAt!);
        }
        return !c.isApplied;
      }).length;

  factory DayReview.fromGenerated(wire.GeneratedDayReview g) => DayReview(
        date: g.date,
        title: g.title,
        summaryText: g.summaryText,
        finalText: g.finalText,
        corrections: (g.corrections ?? const []).map(DayReviewCorrection.fromGenerated).toList(),
        keptConversationIds: g.keptConversationIds ?? const [],
        rolledUpConversationIds: g.rolledUpConversationIds ?? const [],
        generatedAt: g.generatedAt?.toLocal(),
        generationCount: g.generationCount,
      );
}

class DayReviewConversation {
  final String id;
  final String title;
  final String emoji;
  final String overview;
  final DateTime? startedAt;
  final DateTime? finishedAt;
  final bool starred;
  final bool rolledUp;

  const DayReviewConversation({
    required this.id,
    required this.title,
    required this.emoji,
    required this.overview,
    this.startedAt,
    this.finishedAt,
    required this.starred,
    required this.rolledUp,
  });

  factory DayReviewConversation.fromGenerated(wire.GeneratedDayReviewConversation g) => DayReviewConversation(
        id: g.id,
        title: g.title,
        emoji: g.emoji,
        overview: g.overview,
        startedAt: g.startedAt?.toLocal(),
        finishedAt: g.finishedAt?.toLocal(),
        starred: g.starred,
        rolledUp: g.rolledUp,
      );
}

class DayReviewDay {
  final String date;
  final DayReview? review;
  final List<DayReviewConversation> conversations;
  final int processingCount;

  const DayReviewDay({required this.date, this.review, required this.conversations, this.processingCount = 0});

  factory DayReviewDay.fromGenerated(wire.GeneratedDayReviewDayResponse g) => DayReviewDay(
        date: g.date,
        review: g.review == null ? null : DayReview.fromGenerated(g.review!),
        conversations: (g.conversations ?? const []).map(DayReviewConversation.fromGenerated).toList(),
        processingCount: g.processingCount,
      );
}
