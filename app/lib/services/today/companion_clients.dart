import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// Coinpile's read-only summary (`GET <coinpile>/api/v1/me/summary`, a `cp_…` token with the
/// `summary` scope; Settings, Home Assistant & API in Coinpile).
class CoinpileSummary {
  const CoinpileSummary({
    this.netWorth,
    this.bills7d,
    this.bills30d,
    this.savingsTotal,
    this.savingsFree,
    this.spentThisMonth,
    this.projectedThisMonth,
    this.nextBillName,
    this.nextBillDate,
    this.nextBillAmount,
    this.alertsTotal = 0,
    this.alertsCritical = 0,
    this.asOf,
  });

  final double? netWorth;
  final double? bills7d;
  final double? bills30d;
  final double? savingsTotal;
  final double? savingsFree;
  final double? spentThisMonth;
  final double? projectedThisMonth;
  final String? nextBillName;

  /// `yyyy-MM-dd` as Coinpile sends it.
  final String? nextBillDate;
  final double? nextBillAmount;
  final int alertsTotal;
  final int alertsCritical;
  final String? asOf;

  bool get isEmpty => netWorth == null && bills30d == null && savingsTotal == null && spentThisMonth == null;

  static double? _num(Object? v) => v is num ? v.toDouble() : null;

  static CoinpileSummary fromJson(Map<String, Object?> json) {
    final next = json['next_bill'];
    return CoinpileSummary(
      netWorth: _num(json['net_worth']),
      bills7d: _num(json['bills_7d']),
      bills30d: _num(json['bills_30d']),
      savingsTotal: _num(json['savings_total']),
      savingsFree: _num(json['savings_free']),
      spentThisMonth: _num(json['spend_month_so_far']),
      projectedThisMonth: _num(json['spend_month_projected']),
      nextBillName: next is Map ? next['name'] as String? : null,
      nextBillDate: next is Map ? next['date'] as String? : null,
      nextBillAmount: next is Map ? _num(next['amount']) : null,
      alertsTotal: (json['alerts_total'] as num?)?.toInt() ?? 0,
      alertsCritical: (json['alerts_critical'] as num?)?.toInt() ?? 0,
      asOf: json['as_of'] as String?,
    );
  }
}

/// The Pondering Orb's morning brief for a day (`GET <orb>/api/c/daily/<yyyy-MM-dd>`), written by
/// its Golem from the weather, tasks, calendar and money. Markdown.
class PonderingBrief {
  const PonderingBrief({required this.date, required this.text, this.generatedAt});

  final String date;
  final String text;
  final DateTime? generatedAt;

  static PonderingBrief? fromJson(Map<String, Object?> json) {
    final text = json['brief'];
    if (text is! String || text.trim().isEmpty) return null;
    final at = json['generatedAt'];
    return PonderingBrief(
      date: '${json['date'] ?? ''}',
      text: text.trim(),
      generatedAt: at is num ? DateTime.fromMillisecondsSinceEpoch(at.toInt()) : null,
    );
  }

  /// The brief as plain lines for a card: markdown marks removed, blank lines dropped.
  static List<String> plainLines(String markdown) => [
        for (final line in markdown.split('\n'))
          if (_plain(line) case final text when text.isNotEmpty) text,
      ];

  static String _plain(String line) => line
      .replaceAll(RegExp(r'^\s{0,3}#{1,6}\s*'), '')
      .replaceAll(RegExp(r'^\s*[-*+]\s+'), '• ')
      .replaceAll(RegExp(r'\*\*|__|`'), '')
      .replaceAllMapped(RegExp(r'(?<![*\w])[*_]([^*_]+)[*_](?![*\w])'), (m) => m[1]!)
      .replaceAllMapped(RegExp(r'\[([^\]]+)\]\([^)]*\)'), (m) => m[1]!)
      .trim();
}

/// What a companion read came to.
sealed class CompanionResult<T> {
  const CompanionResult();
}

class CompanionData<T> extends CompanionResult<T> {
  const CompanionData(this.data);
  final T data;
}

/// The service answered, with nothing to show (no brief written yet today).
class CompanionNothing<T> extends CompanionResult<T> {
  const CompanionNothing();
}

class CompanionFailure<T> extends CompanionResult<T> {
  const CompanionFailure(this.kind, [this.status]);

  /// `unauthorized` (401/403: token missing, wrong or without the scope), `unreachable`, `bad_response`.
  final String kind;
  final int? status;
}

/// Reads the companion services straight from the phone, the way the Pondering Orb reads them.
class CompanionClients {
  CompanionClients({http.Client? client, this.timeout = const Duration(seconds: 10)}) : _client = client;

  final http.Client? _client;
  final Duration timeout;

  Future<CompanionResult<CoinpileSummary>> coinpile(String baseUrl, String? token) async {
    final result = await _getJson(Uri.parse('$baseUrl/api/v1/me/summary'), token);
    return switch (result) {
      (final Map<String, Object?> json, _) => CompanionData(CoinpileSummary.fromJson(json)),
      (null, final int status) when status == 404 => const CompanionNothing(),
      (null, final int status) => _failure(status),
      _ => const CompanionFailure('unreachable'),
    };
  }

  Future<CompanionResult<PonderingBrief>> pondering(String baseUrl, String? token, {required String day}) async {
    final result = await _getJson(Uri.parse('$baseUrl/api/c/daily/${Uri.encodeComponent(day)}'), token);
    return switch (result) {
      (final Map<String, Object?> json, _) => switch (PonderingBrief.fromJson(json)) {
          final brief? => CompanionData(brief),
          null => const CompanionNothing(),
        },
      (null, final int status) when status == 404 => const CompanionNothing(),
      (null, final int status) => _failure(status),
      _ => const CompanionFailure('unreachable'),
    };
  }

  static CompanionFailure<T> _failure<T>(int status) => status == 401 || status == 403
      ? CompanionFailure('unauthorized', status)
      : status == 0
          ? const CompanionFailure('bad_response')
          : CompanionFailure('unreachable', status);

  /// (json, status): json on a 2xx JSON object, status 0 for a 2xx that is not one, null status
  /// when the request never got an answer.
  Future<(Map<String, Object?>?, int?)> _getJson(Uri uri, String? token) async {
    final client = _client ?? http.Client();
    try {
      final response = await client.get(uri, headers: {
        'Accept': 'application/json',
        if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
      }).timeout(timeout);
      if (response.statusCode < 200 || response.statusCode >= 300) return (null, response.statusCode);
      final decoded = jsonDecode(utf8.decode(response.bodyBytes));
      if (decoded is Map) return (Map<String, Object?>.from(decoded), response.statusCode);
      return (null, 0);
    } on FormatException {
      return (null, 0);
    } catch (_) {
      return (null, null);
    } finally {
      if (_client == null) client.close();
    }
  }
}
