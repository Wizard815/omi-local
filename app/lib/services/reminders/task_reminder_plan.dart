import 'package:omi/backend/schema/action_item.dart';

/// Which of a task's two phone reminders a notification is: at the due time, or the lead time
/// before it (Settings, Notifications, Task reminders).
enum TaskReminderKind { due, lead }

/// What the user chose in Settings: whether open tasks with a due time remind on this phone, and
/// how long before the due time the extra reminder fires (0 = only at the due time).
class TaskReminderSettings {
  const TaskReminderSettings({this.enabled = true, this.leadMinutes = defaultLeadMinutes});

  final bool enabled;
  final int leadMinutes;

  static const defaultLeadMinutes = 15;

  /// The lead times the setting offers, in minutes.
  static const leadChoices = [0, 5, 10, 15, 30, 60, 120, 1440];

  TaskReminderSettings copyWith({bool? enabled, int? leadMinutes}) =>
      TaskReminderSettings(enabled: enabled ?? this.enabled, leadMinutes: leadMinutes ?? this.leadMinutes);

  @override
  bool operator ==(Object other) =>
      other is TaskReminderSettings && other.enabled == enabled && other.leadMinutes == leadMinutes;

  @override
  int get hashCode => Object.hash(enabled, leadMinutes);
}

/// One local notification the phone should hold for a task.
class PlannedTaskReminder {
  const PlannedTaskReminder({
    required this.notificationId,
    required this.taskId,
    required this.kind,
    required this.fireAt,
    required this.description,
    required this.dueAt,
  });

  final int notificationId;
  final String taskId;
  final TaskReminderKind kind;

  /// Local time the notification fires.
  final DateTime fireAt;
  final String description;

  /// The task's own due moment (local), for the lead reminder's wording.
  final DateTime dueAt;

  /// Changes whenever the notification would have to be scheduled again.
  String signature({required bool exact}) =>
      '$taskId|${kind.name}|${fireAt.millisecondsSinceEpoch}|${exact ? 1 : 0}|${_fnv(description)}';
}

/// Which open tasks remind, and when. Pure: the platform scheduling lives behind
/// `TaskReminderPlatform` (task_reminder_scheduler.dart).
abstract final class TaskReminderPlan {
  /// A task due on a date with no time reminds at this local hour.
  static const dateOnlyHour = 9;

  /// At most this many notifications are kept scheduled (the soonest), well under Android's
  /// per-app alarm limit.
  static const maxScheduled = 48;

  /// The stable notification id of [taskId]'s [kind] reminder: a 30-bit FNV-1a hash of the id,
  /// with bit 30 set for the lead reminder. Always a positive 32-bit int, never 0, and the same on
  /// every run (`String.hashCode` is not guaranteed to be).
  static int notificationId(String taskId, TaskReminderKind kind) {
    final base = _fnv(taskId) & 0x3fffffff;
    final id = kind == TaskReminderKind.lead ? base | 0x40000000 : base;
    return id == 0 ? 1 : id;
  }

  /// The moment [task] reminds at, local, or null when it does not remind: done, waiting for the
  /// user's OK, or with no due date.
  static DateTime? dueMoment(ActionItemWithMetadata task) {
    if (task.completed) return null;
    if (task.approval == 'pending' || task.approval == 'declined') return null;
    final due = task.dueAt;
    if (due == null) return null;
    final local = due.toLocal();
    if (task.due?.kind == 'date') {
      final date = DateTime.tryParse(task.due?.date ?? '');
      final day = date ?? local;
      return DateTime(day.year, day.month, day.day, dateOnlyHour);
    }
    return local;
  }

  static List<PlannedTaskReminder> plan(
    Iterable<ActionItemWithMetadata> tasks,
    TaskReminderSettings settings, {
    required DateTime now,
  }) {
    if (!settings.enabled) return const [];
    final seen = <String>{};
    final out = <PlannedTaskReminder>[];
    for (final task in tasks) {
      if (task.id.isEmpty || !seen.add(task.id)) continue;
      final due = dueMoment(task);
      if (due == null) continue;
      final description = task.description.trim();
      if (due.isAfter(now)) {
        out.add(PlannedTaskReminder(
          notificationId: notificationId(task.id, TaskReminderKind.due),
          taskId: task.id,
          kind: TaskReminderKind.due,
          fireAt: due,
          description: description,
          dueAt: due,
        ));
      }
      if (settings.leadMinutes > 0) {
        final lead = due.subtract(Duration(minutes: settings.leadMinutes));
        if (lead.isAfter(now)) {
          out.add(PlannedTaskReminder(
            notificationId: notificationId(task.id, TaskReminderKind.lead),
            taskId: task.id,
            kind: TaskReminderKind.lead,
            fireAt: lead,
            description: description,
            dueAt: due,
          ));
        }
      }
    }
    out.sort((a, b) => a.fireAt.compareTo(b.fireAt));
    return out.length > maxScheduled ? out.sublist(0, maxScheduled) : out;
  }
}

/// 32-bit FNV-1a (offset basis 2166136261, prime 16777619; written in decimal so no hex literal
/// reads as a colour).
int _fnv(String value) {
  var hash = 2166136261;
  for (final unit in value.codeUnits) {
    hash ^= unit;
    hash = (hash * 16777619) & 0xffffffff;
  }
  return hash;
}
