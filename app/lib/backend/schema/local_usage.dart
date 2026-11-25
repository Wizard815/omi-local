/// The signed-in person's own usage on their self-hosted server (`GET /v1/users/me/local-usage`).
///
/// The server keeps these counts itself; the app only reads them. Counts that are not positive
/// numbers and events without a name or time are left out.
library;

import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;

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

  factory LocalUsageReport.fromGenerated(wire.GeneratedLocalUsageResponse g) {
    final totals = <LocalUsageCount>[];
    for (final entry in (g.totals ?? const <String, dynamic>{}).entries) {
      final count = entry.value;
      if (count is num && count > 0) totals.add(LocalUsageCount(event: entry.key, count: count.toInt()));
    }
    totals.sort((a, b) => b.count != a.count ? b.count.compareTo(a.count) : a.event.compareTo(b.event));
    final recent = <LocalUsageEvent>[];
    for (final item in g.recent ?? const <wire.GeneratedLocalUsageEvent>[]) {
      final at = DateTime.tryParse(item.at);
      if (item.event.isNotEmpty && at != null) recent.add(LocalUsageEvent(event: item.event, at: at));
    }
    recent.sort((a, b) => b.at.compareTo(a.at));
    return LocalUsageReport(
      enabled: g.enabled,
      windowDays: g.windowDays > 0 ? g.windowDays : 30,
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
