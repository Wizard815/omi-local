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
      );
}

class FamilyCalendar {
  final bool configured;
  final String label;
  final List<FamilyCalendarEvent> pending;
  final List<FamilyCalendarEvent> recent;

  const FamilyCalendar({required this.configured, required this.label, required this.pending, required this.recent});

  factory FamilyCalendar.fromGenerated(wire.GeneratedFamilyCalendarResponse g) => FamilyCalendar(
        configured: g.configured,
        label: g.label,
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
