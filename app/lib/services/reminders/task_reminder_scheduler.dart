// Owns the phone's scheduled task reminders: a ledger of what was scheduled, reconciled against
// TaskReminderPlan, so a reminder is never left behind for a completed or deleted task.

import 'dart:convert';
import 'dart:io';

import 'package:awesome_notifications/awesome_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/services/reminders/task_reminder_plan.dart';
import 'package:omi/utils/logger.dart';

/// The words a reminder shows, already in the app's language.
class TaskReminderWords {
  const TaskReminderWords({required this.dueTitle, required this.leadTitle, required this.untitled});

  /// "Task due".
  final String dueTitle;

  /// "Due in 15 minutes" for a lead of [minutes].
  final String Function(int minutes) leadTitle;

  /// Body for a task with no description.
  final String untitled;
}

/// The phone's notification scheduler, behind a seam so the reconcile logic is tested without a
/// device. Production: [AwesomeTaskReminderPlatform].
abstract class TaskReminderPlatform {
  /// Whether the app may post notifications at all (Android 13+ asks).
  Future<bool> notificationsAllowed();

  /// Whether alarms may fire at the exact minute (Android 12+ "Alarms & reminders"). False falls
  /// back to an inexact alarm the system may delay a little.
  Future<bool> exactAlarmsAllowed();

  Future<void> schedule(
    PlannedTaskReminder reminder, {
    required bool exact,
    required String title,
    required String body,
  });

  Future<void> cancel(int notificationId);
}

/// What was scheduled last time (notification id → signature and task), kept across launches so
/// a reconcile knows what to cancel.
abstract class TaskReminderLedger {
  Future<Map<int, LedgerEntry>> read();
  Future<void> write(Map<int, LedgerEntry> entries);
}

class LedgerEntry {
  const LedgerEntry({required this.taskId, required this.fireAtMs, required this.signature});

  final String taskId;
  final int fireAtMs;
  final String signature;

  Map<String, Object?> toJson() => {'task': taskId, 'at': fireAtMs, 'sig': signature};

  static LedgerEntry? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final task = raw['task'], at = raw['at'], sig = raw['sig'];
    if (task is! String || at is! int || sig is! String) return null;
    return LedgerEntry(taskId: task, fireAtMs: at, signature: sig);
  }
}

/// What a reconcile did.
class TaskReminderOutcome {
  const TaskReminderOutcome({
    this.scheduled = 0,
    this.cancelled = 0,
    this.kept = 0,
    this.notificationsBlocked = false,
    this.exact = true,
  });

  final int scheduled;
  final int cancelled;

  /// Already scheduled and unchanged.
  final int kept;
  final bool notificationsBlocked;
  final bool exact;
}

/// Keeps the phone's scheduled task reminders equal to [TaskReminderPlan] for the tasks the app
/// knows: schedules new or moved ones, cancels those of tasks that were completed, deleted or
/// lost their due time. A task that is merely not in the loaded page (the task list pages) keeps
/// its reminder unless the list is [complete].
class TaskReminderScheduler {
  TaskReminderScheduler({required this.platform, required this.ledger, DateTime Function()? now})
      : _now = now ?? DateTime.now;

  final TaskReminderPlatform platform;
  final TaskReminderLedger ledger;
  final DateTime Function() _now;
  Future<void> _queue = Future.value();

  /// Runs [work] after any reconcile already in flight, so two never interleave on the ledger.
  Future<T> _serial<T>(Future<T> Function() work) {
    final result = _queue.then((_) => work());
    _queue = result.then((_) {}, onError: (_) {});
    return result;
  }

  Future<TaskReminderOutcome> reconcile(
    List<ActionItemWithMetadata> tasks, {
    required TaskReminderSettings settings,
    required TaskReminderWords words,
    required bool complete,
  }) =>
      _serial(() => _reconcile(tasks, settings: settings, words: words, complete: complete));

  Future<TaskReminderOutcome> _reconcile(
    List<ActionItemWithMetadata> tasks, {
    required TaskReminderSettings settings,
    required TaskReminderWords words,
    required bool complete,
  }) async {
    final now = _now();
    final entries = await ledger.read();
    if (!settings.enabled) {
      final cancelled = await _cancelAll(entries);
      return TaskReminderOutcome(cancelled: cancelled);
    }
    if (!await platform.notificationsAllowed()) {
      return const TaskReminderOutcome(notificationsBlocked: true);
    }
    final exact = await platform.exactAlarmsAllowed();
    final planned = TaskReminderPlan.plan(tasks, settings, now: now);
    final wanted = {for (final r in planned) r.notificationId: r};
    final known = {for (final t in tasks) t.id};
    var cancelled = 0, scheduled = 0, kept = 0;

    for (final MapEntry(key: id, value: entry) in entries.entries.toList()) {
      if (wanted.containsKey(id)) continue;
      final fired = entry.fireAtMs <= now.millisecondsSinceEpoch;
      // Keep a reminder for a task outside the loaded page: it may still be open.
      if (!complete && !known.contains(entry.taskId) && !fired) continue;
      if (!fired) {
        await platform.cancel(id);
        cancelled++;
      }
      entries.remove(id);
    }

    for (final reminder in planned) {
      final signature = reminder.signature(exact: exact);
      if (entries[reminder.notificationId]?.signature == signature) {
        kept++;
        continue;
      }
      final body = reminder.description.isEmpty ? words.untitled : reminder.description;
      final title = reminder.kind == TaskReminderKind.lead ? words.leadTitle(settings.leadMinutes) : words.dueTitle;
      try {
        await platform.schedule(reminder, exact: exact, title: title, body: body);
        entries[reminder.notificationId] = LedgerEntry(
          taskId: reminder.taskId,
          fireAtMs: reminder.fireAt.millisecondsSinceEpoch,
          signature: signature,
        );
        scheduled++;
      } catch (e) {
        Logger.debug('TaskReminderScheduler: scheduling ${reminder.notificationId} failed: $e');
      }
    }
    await ledger.write(entries);
    return TaskReminderOutcome(scheduled: scheduled, cancelled: cancelled, kept: kept, exact: exact);
  }

  /// A task was completed or deleted: its reminders go now, whatever page it was on.
  Future<void> forget(String taskId) => _serial(() async {
        final entries = await ledger.read();
        for (final kind in TaskReminderKind.values) {
          final id = TaskReminderPlan.notificationId(taskId, kind);
          if (entries.remove(id) != null) await platform.cancel(id);
        }
        await ledger.write(entries);
      });

  /// Signing out or turning reminders off: nothing of this account stays scheduled.
  Future<void> cancelAll() => _serial(() async {
        await _cancelAll(await ledger.read());
      });

  Future<int> _cancelAll(Map<int, LedgerEntry> entries) async {
    for (final id in entries.keys) {
      await platform.cancel(id);
    }
    final count = entries.length;
    if (count > 0) await ledger.write(const {});
    return count;
  }
}

/// The ledger in SharedPreferences. At most [TaskReminderPlan.maxScheduled] plus a few stale
/// entries, so it stays a few kilobytes.
class PrefsTaskReminderLedger implements TaskReminderLedger {
  static const key = 'task_reminders_ledger_v1';

  @override
  Future<Map<int, LedgerEntry>> read() async {
    try {
      final raw = (await SharedPreferences.getInstance()).getString(key);
      if (raw == null || raw.isEmpty) return {};
      final decoded = jsonDecode(raw);
      if (decoded is! Map) return {};
      final out = <int, LedgerEntry>{};
      for (final MapEntry(:key, :value) in decoded.entries) {
        final id = int.tryParse('$key');
        final entry = LedgerEntry.fromJson(value);
        if (id != null && entry != null) out[id] = entry;
      }
      return out;
    } catch (_) {
      return {};
    }
  }

  @override
  Future<void> write(Map<int, LedgerEntry> entries) async {
    final prefs = await SharedPreferences.getInstance();
    if (entries.isEmpty) {
      await prefs.remove(key);
      return;
    }
    await prefs.setString(key, jsonEncode({for (final e in entries.entries) '${e.key}': e.value.toJson()}));
  }
}

/// awesome_notifications, the plugin the app already uses for every local notification.
class AwesomeTaskReminderPlatform implements TaskReminderPlatform {
  AwesomeTaskReminderPlatform({AwesomeNotifications? notifications, this.channelKey = 'channel'})
      : _notifications = notifications ?? AwesomeNotifications();

  final AwesomeNotifications _notifications;
  final String channelKey;

  @override
  Future<bool> notificationsAllowed() => _notifications.isNotificationAllowed();

  @override
  Future<bool> exactAlarmsAllowed() async {
    if (!Platform.isAndroid) return true;
    try {
      final granted = await _notifications.checkPermissionList(
        channelKey: channelKey,
        permissions: const [NotificationPermission.PreciseAlarms],
      );
      return granted.contains(NotificationPermission.PreciseAlarms);
    } catch (_) {
      return false;
    }
  }

  @override
  Future<void> schedule(
    PlannedTaskReminder reminder, {
    required bool exact,
    required String title,
    required String body,
  }) async {
    await _notifications.createNotification(
      content: NotificationContent(
        id: reminder.notificationId,
        channelKey: channelKey,
        title: title,
        body: body,
        payload: {'action_item_id': reminder.taskId, 'navigate_to': '/task/${Uri.encodeComponent(reminder.taskId)}'},
        category: NotificationCategory.Reminder,
        wakeUpScreen: true,
      ),
      schedule: NotificationCalendar.fromDate(date: reminder.fireAt, allowWhileIdle: true, preciseAlarm: exact),
    );
  }

  @override
  Future<void> cancel(int notificationId) => _notifications.cancel(notificationId);

  /// Asks for notifications (the Android 13+ prompt) when they are off. True when allowed after.
  Future<bool> requestNotifications() async {
    if (await _notifications.isNotificationAllowed()) return true;
    return _notifications.requestPermissionToSendNotifications(channelKey: channelKey);
  }

  /// Opens the system's "Alarms & reminders" switch for Omi (Android 12+).
  Future<bool> requestExactAlarms() async {
    if (!Platform.isAndroid) return true;
    await _notifications.requestPermissionToSendNotifications(
      channelKey: channelKey,
      permissions: const [NotificationPermission.PreciseAlarms],
    );
    return exactAlarmsAllowed();
  }
}
