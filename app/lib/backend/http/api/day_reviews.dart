import 'dart:convert';

import 'package:omi/backend/http/shared.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/backend/schema/gen/misc_wire.g.dart' as wire;
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/env/env.dart';
import 'package:omi/utils/logger.dart';

/// The result of a day-review call: the value, or the server's error message.
class DayReviewResult<T> {
  final T? value;
  final String? error;

  /// The HTTP status of a failure (409: e.g. a rewrite would replace the user's edits).
  final int? statusCode;

  const DayReviewResult.ok(T this.value)
      : error = null,
        statusCode = null;

  const DayReviewResult.failed(this.error, {this.statusCode}) : value = null;

  bool get ok => value != null;
}

String _base() => '${Env.apiBaseUrl}v1/users/day-reviews';

/// Decode a 200 body with [parse] (which goes through a generated wire DTO), or
/// surface the server's `detail` message for anything else.
DayReviewResult<T> _decode<T>(dynamic response, T Function(Map<String, dynamic>) parse, String label) {
  if (response == null) return const DayReviewResult.failed(null);
  final body = utf8.decode(response.bodyBytes);
  if (response.statusCode == 200) {
    return DayReviewResult.ok(parse(jsonDecode(body) as Map<String, dynamic>));
  }
  Logger.debug('$label failed: ${response.statusCode} $body');
  final int status = response.statusCode;
  try {
    final detail = wire.GeneratedErrorResponse.fromJson(jsonDecode(body) as Map<String, dynamic>).detail;
    return DayReviewResult.failed(detail is String ? detail : null, statusCode: status);
  } catch (_) {
    return DayReviewResult.failed(null, statusCode: status);
  }
}

Future<({String today, List<String> pendingDates, List<String> draftDates})?> getPendingDayReviews(
    {int days = 7}) async {
  final response = await makeApiCall(url: '${_base()}/pending?days=$days', headers: {}, method: 'GET', body: '');
  final result = _decode(
    response,
    (json) {
      final g = wire.GeneratedPendingDayReviewsResponse.fromJson(json);
      return (
        today: g.today,
        pendingDates: g.pendingDates ?? const <String>[],
        draftDates: g.draftDates ?? const <String>[],
      );
    },
    'getPendingDayReviews',
  );
  return result.value;
}

Future<List<DayReview>> listDayReviews({int limit = 30}) async {
  final response =
      await makeApiCall(url: '${_base()}?limit=$limit&include_drafts=true', headers: {}, method: 'GET', body: '');
  final result = _decode(
    response,
    (json) =>
        (wire.GeneratedDayReviewsListResponse.fromJson(json).reviews ?? const []).map(DayReview.fromGenerated).toList(),
    'listDayReviews',
  );
  return result.value ?? const [];
}

Future<DayReviewResult<DayReviewDay>> getDayReviewDay(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date?include_drafts=true', headers: {}, method: 'GET', body: '');
  return _decode(
    response,
    (json) => DayReviewDay.fromGenerated(wire.GeneratedDayReviewDayResponse.fromJson(json)),
    'getDayReviewDay',
  );
}

Future<DayReviewResult<DayReview>> finalizeDayReview(
  String date,
  List<String> keepConversationIds, {
  List<List<String>> mergeGroups = const [],
  List<String> dropConversationIds = const [],
}) async {
  final response = await makeApiCall(
    url: '${_base()}/$date/finalize',
    headers: {'Content-Type': 'application/json'},
    method: 'POST',
    body: jsonEncode({
      'keep_conversation_ids': keepConversationIds,
      'merge_groups': [
        for (final g in mergeGroups) {'conversation_ids': g}
      ],
      'drop_conversation_ids': dropConversationIds,
    }),
  );
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'finalizeDayReview');
}

String _replaceEdits(bool replaceEdits) => replaceEdits ? '?replace_edits=true' : '';

/// Approve the day's draft: finalize with its split and text; only then does its summary go to the calendar.
/// [replaceEdits]: the user agreed that a rewrite (the day changed since they edited it) replaces their edits.
Future<DayReviewResult<DayReview>> approveDayReview(String date, {bool replaceEdits = false}) async {
  final response = await makeApiCall(
      url: '${_base()}/$date/approve${_replaceEdits(replaceEdits)}', headers: {}, method: 'POST', body: '');
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'approveDayReview');
}

/// Pull a section's conversations back out as starred conversations ([favorited] = true) or fold them back in.
Future<DayReviewResult<DayReview>> favoriteDayReviewSection(String date, String sectionId, bool favorited) async {
  final response = await makeApiCall(
    url: '${_base()}/$date/sections/$sectionId/favorite',
    headers: {'Content-Type': 'application/json'},
    method: 'POST',
    body: jsonEncode({'favorited': favorited}),
  );
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'favoriteDayReviewSection');
}

/// Keep one clip of the summary as its own (starred) conversation ([kept] = true) or fold it back in.
Future<DayReviewResult<DayReview>> keepDayReviewConversation(String date, String conversationId, bool kept) async {
  final response = await makeApiCall(
    url: '${_base()}/$date/conversations/$conversationId/keep',
    headers: {'Content-Type': 'application/json'},
    method: 'POST',
    body: jsonEncode({'kept': kept}),
  );
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'keepDayReviewConversation');
}

Future<DayReviewResult<DayReview>> addDayReviewCorrection(
  String date, {
  required DayReviewCorrectionType type,
  String? quote,
  int? start,
  int? end,
  String? text,
  int? afterParagraph,
}) async {
  final response = await makeApiCall(
    url: '${_base()}/$date/corrections',
    headers: {'Content-Type': 'application/json'},
    method: 'POST',
    body: jsonEncode({
      'type': type.name,
      if (quote != null) 'quote': quote,
      if (start != null) 'start': start,
      if (end != null) 'end': end,
      if (text != null) 'text': text,
      if (afterParagraph != null) 'after_paragraph': afterParagraph,
    }),
  );
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'addDayReviewCorrection');
}

Future<DayReviewResult<DayReview>> deleteDayReviewCorrection(String date, String correctionId) async {
  final response =
      await makeApiCall(url: '${_base()}/$date/corrections/$correctionId', headers: {}, method: 'DELETE', body: '');
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'deleteDayReviewCorrection');
}

Future<DayReviewResult<DayReview>> regenerateDayReview(String date, {bool replaceEdits = false}) async {
  final response = await makeApiCall(
      url: '${_base()}/$date/regenerate${_replaceEdits(replaceEdits)}', headers: {}, method: 'POST', body: '');
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'regenerateDayReview');
}

/// The user's own words: the title and changed sections ({id: (title, text)}), or the whole text of
/// a summary without sections.
Future<DayReviewResult<DayReview>> editDayReviewText(
  String date, {
  String? title,
  Map<String, ({String title, String text})>? sections,
  String? summaryText,
}) async {
  final response = await makeApiCall(
    url: '${_base()}/$date/text',
    headers: {'Content-Type': 'application/json'},
    method: 'PUT',
    body: jsonEncode({
      if (title != null) 'title': title,
      if (sections != null)
        'sections': [
          for (final entry in sections.entries) {'id': entry.key, 'title': entry.value.title, 'text': entry.value.text}
        ],
      if (summaryText != null) 'summary_text': summaryText,
    }),
  );
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'editDayReviewText');
}

Future<DayReviewResult<int>> reopenDayReview(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date/reopen', headers: {}, method: 'POST', body: '');
  return _decode(
      response, (json) => wire.GeneratedReopenDayReviewResponse.fromJson(json).restoredCount, 'reopenDayReview');
}

/// The calls [DayReviewPage] makes, as one object so widget tests can stand in for the server.
class DayReviewApi {
  const DayReviewApi();

  Future<DayReviewResult<DayReviewDay>> getDay(String date) => getDayReviewDay(date);

  Future<DayReviewResult<DayReview>> finalize(
    String date,
    List<String> keep, {
    List<List<String>> mergeGroups = const [],
    List<String> drop = const [],
  }) =>
      finalizeDayReview(date, keep, mergeGroups: mergeGroups, dropConversationIds: drop);

  Future<DayReviewResult<DayReview>> approve(String date, {bool replaceEdits = false}) =>
      approveDayReview(date, replaceEdits: replaceEdits);

  Future<DayReviewResult<DayReview>> favoriteSection(String date, String sectionId, bool favorited) =>
      favoriteDayReviewSection(date, sectionId, favorited);

  Future<DayReviewResult<DayReview>> keepConversation(String date, String conversationId, bool kept) =>
      keepDayReviewConversation(date, conversationId, kept);

  Future<DayReviewResult<DayReview>> addCorrection(
    String date, {
    required DayReviewCorrectionType type,
    String? quote,
    int? start,
    int? end,
    String? text,
    int? afterParagraph,
  }) =>
      addDayReviewCorrection(date,
          type: type, quote: quote, start: start, end: end, text: text, afterParagraph: afterParagraph);

  Future<DayReviewResult<DayReview>> deleteCorrection(String date, String correctionId) =>
      deleteDayReviewCorrection(date, correctionId);

  Future<DayReviewResult<DayReview>> regenerate(String date, {bool replaceEdits = false}) =>
      regenerateDayReview(date, replaceEdits: replaceEdits);

  Future<DayReviewResult<DayReview>> editText(
    String date, {
    String? title,
    Map<String, ({String title, String text})>? sections,
    String? summaryText,
  }) =>
      editDayReviewText(date, title: title, sections: sections, summaryText: summaryText);

  Future<DayReviewResult<int>> reopen(String date) => reopenDayReview(date);
}
