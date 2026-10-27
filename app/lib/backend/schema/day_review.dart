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

/// One part of the day summary and the conversations it was written from.
class DayReviewSection {
  final String id;
  final List<String> conversationIds;
  final String title;
  final String text;
  final List<String> tags;

  /// Its conversations were pulled back out as starred conversations.
  final bool favorited;

  const DayReviewSection({
    required this.id,
    required this.conversationIds,
    required this.title,
    required this.text,
    this.tags = const [],
    this.favorited = false,
  });

  factory DayReviewSection.fromGenerated(wire.GeneratedDayReviewSection g) => DayReviewSection(
        id: g.id,
        conversationIds: g.conversationIds ?? const [],
        title: g.title,
        text: g.text,
        tags: g.tags ?? const [],
        favorited: g.favorited,
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

  /// summaryText split by source conversations; empty for reviews written before sections existed.
  final List<DayReviewSection> sections;

  /// Every section tag of the day.
  final List<String> tags;
  final List<String> keptConversationIds;
  final List<String> rolledUpConversationIds;

  /// Each group was merged into one new conversation; the sources are gone.
  final List<List<String>> mergedGroups;
  final List<String> droppedConversationIds;
  final DateTime? generatedAt;
  final int generationCount;

  const DayReview({
    required this.date,
    required this.title,
    required this.summaryText,
    required this.finalText,
    required this.corrections,
    this.sections = const [],
    this.tags = const [],
    required this.keptConversationIds,
    required this.rolledUpConversationIds,
    this.mergedGroups = const [],
    this.droppedConversationIds = const [],
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
        sections: (g.sections ?? const []).map(DayReviewSection.fromGenerated).toList(),
        tags: g.tags ?? const [],
        keptConversationIds: g.keptConversationIds ?? const [],
        rolledUpConversationIds: g.rolledUpConversationIds ?? const [],
        mergedGroups: (g.mergedGroups ?? const []).map((group) => group.conversationIds ?? const <String>[]).toList(),
        droppedConversationIds: g.droppedConversationIds ?? const [],
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
  final List<String> tags;
  final bool rolledUp;
  final bool dropped;

  /// Transcript only so far (the server held it for this review): no overview yet.
  final bool held;

  /// The noise rule that let a held clip through; `model_withheld` means short or unclear.
  final String holdRule;
  final String transcriptSnippet;
  final int wordCount;

  bool get unclear => held && holdRule == 'model_withheld';

  const DayReviewConversation({
    required this.id,
    required this.title,
    required this.emoji,
    required this.overview,
    this.startedAt,
    this.finishedAt,
    required this.starred,
    this.tags = const [],
    required this.rolledUp,
    this.dropped = false,
    this.held = false,
    this.holdRule = '',
    this.transcriptSnippet = '',
    this.wordCount = 0,
  });

  factory DayReviewConversation.fromGenerated(wire.GeneratedDayReviewConversation g) => DayReviewConversation(
        id: g.id,
        title: g.title,
        emoji: g.emoji,
        overview: g.overview,
        startedAt: g.startedAt?.toLocal(),
        finishedAt: g.finishedAt?.toLocal(),
        starred: g.starred,
        tags: g.tags ?? const [],
        rolledUp: g.rolledUp,
        dropped: g.dropped,
        held: g.held,
        holdRule: g.holdRule,
        transcriptSnippet: g.transcriptSnippet,
        wordCount: g.wordCount,
      );
}

class DayReviewDay {
  final String date;
  final DayReview? review;
  final List<DayReviewConversation> conversations;
  final int processingCount;

  /// Clips the server's noise rules dropped on their own.
  final int droppedAutoCount;

  /// Finished clips wait for this review instead of being processed one by one.
  final bool holdEnabled;

  const DayReviewDay({
    required this.date,
    this.review,
    required this.conversations,
    this.processingCount = 0,
    this.droppedAutoCount = 0,
    this.holdEnabled = false,
  });

  factory DayReviewDay.fromGenerated(wire.GeneratedDayReviewDayResponse g) => DayReviewDay(
        date: g.date,
        review: g.review == null ? null : DayReview.fromGenerated(g.review!),
        conversations: (g.conversations ?? const []).map(DayReviewConversation.fromGenerated).toList(),
        processingCount: g.processingCount,
        droppedAutoCount: g.droppedAutoCount,
        holdEnabled: g.holdEnabled,
      );
}
