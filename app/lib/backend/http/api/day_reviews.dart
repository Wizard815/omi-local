import 'dart:convert';

import 'package:omi/backend/http/shared.dart';
import 'package:omi/backend/schema/day_review.dart';
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

DayReviewResult<T> _decode<T>(dynamic response, T Function(Map<String, dynamic>) parse, String label) {
  if (response == null) return const DayReviewResult.failed(null);
  final body = utf8.decode(response.bodyBytes);
  if (response.statusCode == 200) {
    return DayReviewResult.ok(parse(jsonDecode(body) as Map<String, dynamic>));
  }
  Logger.debug('$label failed: ${response.statusCode} $body');
  String? detail;
  try {
    final decoded = jsonDecode(body);
    if (decoded is Map && decoded['detail'] is String) detail = decoded['detail'] as String;
  } catch (_) {}
  return DayReviewResult.failed(detail);
}

Future<({String today, List<String> pendingDates})?> getPendingDayReviews({int days = 7}) async {
  final response = await makeApiCall(url: '${_base()}/pending?days=$days', headers: {}, method: 'GET', body: '');
  final result = _decode(
    response,
    (json) => (
      today: json['today'] as String,
      pendingDates: ((json['pending_dates'] as List?) ?? []).cast<String>(),
    ),
    'getPendingDayReviews',
  );
  return result.value;
}

Future<List<DayReview>> listDayReviews({int limit = 30}) async {
  final response = await makeApiCall(url: '${_base()}?limit=$limit', headers: {}, method: 'GET', body: '');
  final result = _decode(
    response,
    (json) => ((json['reviews'] as List?) ?? []).map((e) => DayReview.fromJson(e as Map<String, dynamic>)).toList(),
    'listDayReviews',
  );
  return result.value ?? const [];
}

Future<DayReviewResult<DayReviewDay>> getDayReviewDay(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date', headers: {}, method: 'GET', body: '');
  return _decode(response, DayReviewDay.fromJson, 'getDayReviewDay');
}

Future<DayReviewResult<DayReview>> finalizeDayReview(String date, List<String> keepConversationIds) async {
  final response = await makeApiCall(
    url: '${_base()}/$date/finalize',
    headers: {'Content-Type': 'application/json'},
    method: 'POST',
    body: jsonEncode({'keep_conversation_ids': keepConversationIds}),
  );
  return _decode(response, DayReview.fromJson, 'finalizeDayReview');
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
  return _decode(response, DayReview.fromJson, 'addDayReviewCorrection');
}

Future<DayReviewResult<DayReview>> deleteDayReviewCorrection(String date, String correctionId) async {
  final response =
      await makeApiCall(url: '${_base()}/$date/corrections/$correctionId', headers: {}, method: 'DELETE', body: '');
  return _decode(response, DayReview.fromJson, 'deleteDayReviewCorrection');
}

Future<DayReviewResult<DayReview>> regenerateDayReview(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date/regenerate', headers: {}, method: 'POST', body: '');
  return _decode(response, DayReview.fromJson, 'regenerateDayReview');
}

Future<DayReviewResult<int>> reopenDayReview(String date) async {
  final response = await makeApiCall(url: '${_base()}/$date/reopen', headers: {}, method: 'POST', body: '');
  return _decode(response, (json) => (json['restored_count'] as int?) ?? 0, 'reopenDayReview');
}
