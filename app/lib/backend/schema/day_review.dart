/// End-of-day review (backend: /v1/users/day-reviews). Hand-written models:
/// these routes are not in the generated wire groups.
library;

DateTime? _date(dynamic v) => v is String ? DateTime.tryParse(v)?.toLocal() : null;

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

  factory DayReviewCorrection.fromJson(Map<String, dynamic> json) => DayReviewCorrection(
        id: json['id'] as String,
        type: DayReviewCorrectionType.values.byName(json['type'] as String),
        quote: json['quote'] as String?,
        start: json['start'] as int?,
        end: json['end'] as int?,
        text: json['text'] as String?,
        afterParagraph: json['after_paragraph'] as int?,
        createdAt: _date(json['created_at']),
        appliedAt: _date(json['applied_at']),
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

  factory DayReview.fromJson(Map<String, dynamic> json) => DayReview(
        date: json['date'] as String,
        title: (json['title'] as String?) ?? '',
        summaryText: (json['summary_text'] as String?) ?? '',
        finalText: (json['final_text'] as String?) ?? '',
        corrections: ((json['corrections'] as List?) ?? [])
            .map((e) => DayReviewCorrection.fromJson(e as Map<String, dynamic>))
            .toList(),
        keptConversationIds: ((json['kept_conversation_ids'] as List?) ?? []).cast<String>(),
        rolledUpConversationIds: ((json['rolled_up_conversation_ids'] as List?) ?? []).cast<String>(),
        generatedAt: _date(json['generated_at']),
        generationCount: (json['generation_count'] as int?) ?? 0,
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

  factory DayReviewConversation.fromJson(Map<String, dynamic> json) => DayReviewConversation(
        id: json['id'] as String,
        title: (json['title'] as String?) ?? '',
        emoji: (json['emoji'] as String?) ?? '',
        overview: (json['overview'] as String?) ?? '',
        startedAt: _date(json['started_at']),
        finishedAt: _date(json['finished_at']),
        starred: json['starred'] as bool? ?? false,
        rolledUp: json['rolled_up'] as bool? ?? false,
      );
}

class DayReviewDay {
  final String date;
  final DayReview? review;
  final List<DayReviewConversation> conversations;
  final int processingCount;

  const DayReviewDay({required this.date, this.review, required this.conversations, this.processingCount = 0});

  factory DayReviewDay.fromJson(Map<String, dynamic> json) => DayReviewDay(
        date: json['date'] as String,
        review: json['review'] == null ? null : DayReview.fromJson(json['review'] as Map<String, dynamic>),
        conversations: ((json['conversations'] as List?) ?? [])
            .map((e) => DayReviewConversation.fromJson(e as Map<String, dynamic>))
            .toList(),
        processingCount: (json['processing_count'] as int?) ?? 0,
      );
}
