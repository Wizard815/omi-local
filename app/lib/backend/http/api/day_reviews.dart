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

  const DayReviewResult.ok(T this.value) : error = null;

  const DayReviewResult.failed(this.error) : value = null;

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
  try {
    final detail = wire.GeneratedErrorResponse.fromJson(jsonDecode(body) as Map<String, dynamic>).detail;
    return DayReviewResult.failed(detail is String ? detail : null);
  } catch (_) {
    return const DayReviewResult.failed(null);
  }
}

Future<({String today, List<String> pendingDates})?> getPendingDayReviews({int days = 7}) async {
  final response = await makeApiCall(url: '${_base()}/pending?days=$days', headers: {}, method: 'GET', body: '');
  final result = _decode(
    response,
    (json) {
      final g = wire.GeneratedPendingDayReviewsResponse.fromJson(json);
      return (today: g.today, pendingDates: g.pendingDates ?? const <String>[]);
    },
    'getPendingDayReviews',
  );
  return result.value;
}

Future<List<DayReview>> listDayReviews({int limit = 30}) async {
  final response = await makeApiCall(url: '${_base()}?limit=$limit', headers: {}, method: 'GET', body: '');
  final result = _decode(
    response,
    (json) =>
        (wire.GeneratedDayReviewsListResponse.fromJson(json).reviews ?? const []).map(DayReview.fromGenerated).toList(),
    'listDayReviews',
  );
  return result.value ?? const [];
}

Future<DayReviewResult<DayReviewDay>> getDayReviewDay(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date', headers: {}, method: 'GET', body: '');
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

Future<DayReviewResult<DayReview>> regenerateDayReview(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date/regenerate', headers: {}, method: 'POST', body: '');
  return _decode(
      response, (json) => DayReview.fromGenerated(wire.GeneratedDayReview.fromJson(json)), 'regenerateDayReview');
}

Future<DayReviewResult<int>> reopenDayReview(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date/reopen', headers: {}, method: 'POST', body: '');
  return _decode(
      response, (json) => wire.GeneratedReopenDayReviewResponse.fromJson(json).restoredCount, 'reopenDayReview');
}
