// Pure models behind Omi Today and the Today overview widget: today's agenda, the day in
// conversations and the people worth a glance. No I/O, so both surfaces agree.

import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/backend/schema/person.dart';

/// One thing on today's agenda: a phone calendar event or a family calendar block.
class AgendaEvent {
  const AgendaEvent({required this.title, this.start, this.allDay = false, this.timeLabel = '', this.family = false});

  final String title;

  /// Local; null for an all-day entry.
  final DateTime? start;
  final bool allDay;

  /// The family calendar's own time label ("14:30") when it has no instant.
  final String timeLabel;
  final bool family;
}

/// Today's events and the tasks due, for the Agenda card and the Today overview widget.
class TodayAgenda {
  const TodayAgenda({required this.events, required this.tasks, this.familyUnreachable = false});

  final List<AgendaEvent> events;
  final List<ActionItemWithMetadata> tasks;

  /// The family calendar is set up but did not answer (`reachable: false`): a soft note, not an
  /// error, since the rest of the card still shows.
  final bool familyUnreachable;

  bool get isEmpty => events.isEmpty && tasks.isEmpty;

  static const maxEvents = 8;
  static const maxTasks = 8;

  /// Builds the agenda from the phone's calendar rows (DeviceCalendarService.getEvents: `title`,
  /// `startMs`, `allDay`), today on the family calendar, and the open tasks. Events that already
  /// ended are left out; an all-day event stays all day. A family block with the same title and
  /// start as a phone event (a family calendar synced to the phone) shows once.
  static TodayAgenda build({
    required List<Map<String, dynamic>> deviceEvents,
    FamilyDay? family,
    required List<ActionItemWithMetadata> tasks,
    required DateTime now,
  }) {
    final startOfDay = DateTime(now.year, now.month, now.day);
    final endOfDay = startOfDay.add(const Duration(days: 1));
    final events = <AgendaEvent>[];
    for (final row in deviceEvents) {
      final title = '${row['title'] ?? ''}'.trim();
      final startMs = row['startMs'];
      if (title.isEmpty || startMs is! int) continue;
      final start = DateTime.fromMillisecondsSinceEpoch(startMs);
      final endMs = row['endMs'];
      final allDay = row['allDay'] == true;
      if (allDay) {
        events.add(AgendaEvent(title: title, allDay: true));
        continue;
      }
      final end = endMs is int ? DateTime.fromMillisecondsSinceEpoch(endMs) : start;
      if (start.isBefore(startOfDay) && end.isBefore(now)) continue;
      if (!start.isBefore(endOfDay) || end.isBefore(now)) continue;
      events.add(AgendaEvent(title: title, start: start));
    }
    var familyUnreachable = false;
    if (family != null && family.configured) {
      familyUnreachable = !family.reachable;
      for (final block in family.next) {
        final title = block.title.trim();
        if (title.isEmpty) continue;
        final start = block.start?.toLocal();
        final duplicate = events.any((e) => e.title == title && e.start == start);
        if (duplicate) continue;
        events.add(AgendaEvent(
          title: title,
          start: block.allDay ? null : start,
          allDay: block.allDay,
          timeLabel: block.timeLabel.trim(),
          family: true,
        ));
      }
    }
    // omi-perf-allow: sort-in-build -- one day's events, at most a few dozen
    events.sort((a, b) {
      if (a.start == null && b.start == null) return 0;
      if (a.start == null) return -1;
      if (b.start == null) return 1;
      return a.start!.compareTo(b.start!);
    });
    final due = tasks.where((t) {
      if (t.completed || t.dueAt == null) return false;
      return t.dueAt!.toLocal().isBefore(endOfDay);
    }).toList()
      // omi-perf-allow: sort-in-build -- tasks due by today, bounded by the loaded task page
      ..sort((a, b) => a.dueAt!.compareTo(b.dueAt!));
    return TodayAgenda(
      events: events.take(maxEvents).toList(),
      tasks: due.take(maxTasks).toList(),
      familyUnreachable: familyUnreachable,
    );
  }
}

/// Today in conversations: how many, the newest overview, the latest titles.
class TodayDaySummary {
  const TodayDaySummary({required this.count, this.headline, this.overview, this.recent = const []});

  final int count;
  final String? headline;
  final String? overview;
  final List<ServerConversation> recent;

  bool get isEmpty => count == 0 && overview == null;

  static TodayDaySummary from(List<ServerConversation> conversations, {required DateTime now, int recentLimit = 3}) {
    bool sameDay(DateTime d) => d.year == now.year && d.month == now.month && d.day == now.day;
    final today = conversations
        .where((c) => !c.discarded && !c.deleted && sameDay((c.startedAt ?? c.createdAt).toLocal()))
        .toList();
    String? headline, overview;
    for (final c in today) {
      if (c.status != ConversationStatus.completed) continue;
      final text = c.structured.overview.trim();
      if (text.isEmpty) continue;
      overview = text;
      final title = c.structured.title.trim();
      headline = title.isEmpty ? null : title;
      break;
    }
    return TodayDaySummary(
      count: today.length,
      headline: headline,
      overview: overview,
      recent: today.where((c) => c.structured.title.trim().isNotEmpty).take(recentLimit).toList(),
    );
  }

  /// The one line the Today overview widget shows.
  String? get line {
    if (overview == null) return null;
    final text = overview!.replaceAll(RegExp(r'\s+'), ' ');
    return text.length > 160 ? '${text.substring(0, 159).trimRight()}…' : text;
  }
}

/// The people worth a glance today: pinned first, then the most recently heard.
List<Person> pickTodayPeople(List<Person> people, {int limit = 5}) {
  final sorted = [...people]..sort((a, b) {
      if (a.pinned != b.pinned) return a.pinned ? -1 : 1;
      final ah = a.lastHeardAt, bh = b.lastHeardAt;
      if (ah == null && bh == null) return a.name.compareTo(b.name);
      if (ah == null) return 1;
      if (bh == null) return -1;
      return bh.compareTo(ah);
    });
  return sorted.where((p) => p.name.trim().isNotEmpty).take(limit).toList();
}
