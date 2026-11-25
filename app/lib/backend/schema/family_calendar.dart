/// Family calendar (omi-local, backend: /v1/family-calendar): the events Omi heard, waiting
/// for the user to check the date and time before they go to dayGLANCE through ALLGlance.
library;

import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;

class FamilyCalendarEvent {
  final String conversationId;
  final int eventIdx;
  final String conversationTitle;
  final String title;
  final String description;

  /// Local time.
  final DateTime start;
  final int duration;
  final DateTime? heardAt;
  final bool created;
  final String? calendar;
  final String? externalId;
  final bool declined;

  /// The user said yes: on their CalDAV calendar once synced (with or without dayGLANCE).
  final bool approved;

  const FamilyCalendarEvent({
    required this.conversationId,
    required this.eventIdx,
    required this.conversationTitle,
    required this.title,
    required this.description,
    required this.start,
    required this.duration,
    this.heardAt,
    this.created = false,
    this.calendar,
    this.externalId,
    this.declined = false,
    this.approved = false,
  });

  String get key => '$conversationId:$eventIdx';

  DateTime get end => start.add(Duration(minutes: duration));

  factory FamilyCalendarEvent.fromGenerated(wire.GeneratedFamilyCalendarEvent g) => FamilyCalendarEvent(
        conversationId: g.conversationId,
        eventIdx: g.eventIdx,
        conversationTitle: g.conversationTitle,
        title: g.title,
        description: g.description,
        start: g.start.toLocal(),
        duration: g.duration,
        heardAt: g.heardAt?.toLocal(),
        created: g.created,
        calendar: g.calendar,
        externalId: g.externalId,
        declined: g.declined,
        approved: g.approved,
      );
}

/// A household member from the gateway's roster.
class FamilyCalendarMember {
  final String id;
  final String name;

  const FamilyCalendarMember({required this.id, required this.name});

  factory FamilyCalendarMember.fromGenerated(wire.GeneratedFamilyCalendarMember g) =>
      FamilyCalendarMember(id: g.id, name: g.name);
}

class FamilyCalendar {
  final bool configured;

  /// Approved events also go to the user's CalDAV calendar (Settings, Calendar sync). With no family
  /// calendar gateway, that is what approving means.
  final bool caldav;
  final String label;

  /// The household; empty when the gateway could not be read.
  final List<FamilyCalendarMember> members;

  /// The member Omi adds events as; null = the whole family.
  final String? ownerId;
  final String ownerName;
  final List<FamilyCalendarEvent> pending;
  final List<FamilyCalendarEvent> recent;

  const FamilyCalendar({
    required this.configured,
    this.caldav = false,
    required this.label,
    this.members = const [],
    this.ownerId,
    this.ownerName = '',
    required this.pending,
    required this.recent,
  });

  factory FamilyCalendar.fromGenerated(wire.GeneratedFamilyCalendarResponse g) => FamilyCalendar(
        configured: g.configured,
        caldav: g.caldav,
        label: g.label,
        members: (g.members ?? const []).map(FamilyCalendarMember.fromGenerated).toList(),
        ownerId: g.ownerId,
        ownerName: g.ownerName,
        pending: (g.pending ?? const []).map(FamilyCalendarEvent.fromGenerated).toList(),
        recent: (g.recent ?? const []).map(FamilyCalendarEvent.fromGenerated).toList(),
      );
}

/// What approve/decline answer: the event as stored now, or the server's reason.
class FamilyCalendarOutcome {
  final bool ok;
  final FamilyCalendarEvent? event;
  final String error;

  const FamilyCalendarOutcome({required this.ok, this.event, this.error = ''});

  factory FamilyCalendarOutcome.fromGenerated(wire.GeneratedFamilyCalendarEventResponse g) => FamilyCalendarOutcome(
        ok: g.ok,
        event: g.event == null ? null : FamilyCalendarEvent.fromGenerated(g.event!),
        error: g.error,
      );
}

/// One block still ahead today on the family calendar.
class FamilyDayBlock {
  final String title;

  /// UTC; null for an all-day block.
  final DateTime? start;

  /// "14:30" in the user's time zone, or the server's word for all day.
  final String timeLabel;
  final bool allDay;

  const FamilyDayBlock({required this.title, this.start, this.timeLabel = '', this.allDay = false});

  factory FamilyDayBlock.fromGenerated(wire.GeneratedFamilyDayBlock g) =>
      FamilyDayBlock(title: g.title, start: g.start, timeLabel: g.timeLabel, allDay: g.allDay);
}

/// Today on the family calendar (`GET /v1/family-calendar/today`), sized for a home-screen widget.
class FamilyDay {
  final bool configured;

  /// The gateway answered. When false, a widget keeps its last data and [error] says why.
  final bool reachable;

  /// Today in the user's time zone, `yyyy-MM-dd`.
  final String date;

  /// The member it is for; empty = the whole family.
  final String ownerName;
  final List<FamilyDayBlock> next;

  /// Blocks still ahead today, including those not in [next].
  final int remaining;
  final int tasksDue;
  final int choresOverdue;
  final String error;

  const FamilyDay({
    required this.configured,
    this.reachable = false,
    this.date = '',
    this.ownerName = '',
    this.next = const [],
    this.remaining = 0,
    this.tasksDue = 0,
    this.choresOverdue = 0,
    this.error = '',
  });

  factory FamilyDay.fromGenerated(wire.GeneratedFamilyDayResponse g) => FamilyDay(
        configured: g.configured,
        reachable: g.reachable,
        date: g.date,
        ownerName: g.ownerName,
        next: (g.next ?? const []).map(FamilyDayBlock.fromGenerated).toList(),
        remaining: g.remaining,
        tasksDue: g.tasksDue,
        choresOverdue: g.choresOverdue,
        error: g.error,
      );
}
