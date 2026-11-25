/// The signed-in person's own usage on their self-hosted server (`GET /v1/users/me/local-usage`).
///
/// The server keeps these counts itself; the app only reads them. Unknown or malformed fields
/// read as empty rather than failing the whole page.
class LocalUsageReport {
  const LocalUsageReport({
    required this.enabled,
    required this.windowDays,
    required this.totals,
    required this.recent,
  });

  /// False when the server does not count usage (`OMI_LOCAL_USAGE=off`).
  final bool enabled;

  /// How many days the counts cover.
  final int windowDays;

  /// Count per action, most used first.
  final List<LocalUsageCount> totals;

  /// The newest counted actions, newest first.
  final List<LocalUsageEvent> recent;

  int get total => totals.fold(0, (sum, row) => sum + row.count);

  factory LocalUsageReport.fromJson(Map<String, dynamic> json) {
    final totals = <LocalUsageCount>[];
    final rawTotals = json['totals'];
    if (rawTotals is Map) {
      for (final entry in rawTotals.entries) {
        final count = entry.value;
        if (entry.key is String && count is num && count > 0) {
          totals.add(LocalUsageCount(event: entry.key as String, count: count.toInt()));
        }
      }
    }
    totals.sort((a, b) => b.count != a.count ? b.count.compareTo(a.count) : a.event.compareTo(b.event));
    final recent = <LocalUsageEvent>[];
    final rawRecent = json['recent'];
    if (rawRecent is List) {
      for (final item in rawRecent) {
        if (item is! Map) continue;
        final event = item['event'];
        final at = DateTime.tryParse('${item['at'] ?? ''}');
        if (event is String && event.isNotEmpty && at != null) recent.add(LocalUsageEvent(event: event, at: at));
      }
    }
    recent.sort((a, b) => b.at.compareTo(a.at));
    final window = json['window_days'];
    return LocalUsageReport(
      enabled: json['enabled'] != false,
      windowDays: window is num && window > 0 ? window.toInt() : 30,
      totals: totals,
      recent: recent,
    );
  }
}

class LocalUsageCount {
  const LocalUsageCount({required this.event, required this.count});

  /// The server's action name, such as "Memory Created".
  final String event;
  final int count;
}

class LocalUsageEvent {
  const LocalUsageEvent({required this.event, required this.at});

  final String event;
  final DateTime at;
}
