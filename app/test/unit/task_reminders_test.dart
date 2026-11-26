import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/backend/schema/gen/action_items_folders_wire.g.dart' show GeneratedTaskDue;
import 'package:omi/services/reminders/phone_alarm.dart';
import 'package:omi/services/reminders/task_reminder_coordinator.dart';
import 'package:omi/services/reminders/task_reminder_plan.dart';
import 'package:omi/services/reminders/task_reminder_scheduler.dart';

ActionItemWithMetadata task(
  String id, {
  DateTime? dueAt,
  bool completed = false,
  String description = 'Call the dentist',
  String? approval,
  GeneratedTaskDue? due,
}) =>
    ActionItemWithMetadata(
      id: id,
      description: description,
      completed: completed,
      dueAt: dueAt,
      approval: approval,
      due: due,
    );

class FakePlatform implements TaskReminderPlatform {
  bool allowed = true;
  bool exact = true;
  final Map<int, ({DateTime at, bool exact, String title, String body})> scheduled = {};
  final List<int> cancelled = [];

  @override
  Future<bool> notificationsAllowed() async => allowed;

  @override
  Future<bool> exactAlarmsAllowed() async => exact;

  @override
  Future<void> schedule(PlannedTaskReminder reminder,
      {required bool exact, required String title, required String body}) async {
    scheduled[reminder.notificationId] = (at: reminder.fireAt, exact: exact, title: title, body: body);
  }

  @override
  Future<void> cancel(int notificationId) async {
    cancelled.add(notificationId);
    scheduled.remove(notificationId);
  }
}

class MemoryLedger implements TaskReminderLedger {
  Map<int, LedgerEntry> entries = {};

  @override
  Future<Map<int, LedgerEntry>> read() async => Map.of(entries);

  @override
  Future<void> write(Map<int, LedgerEntry> next) async => entries = Map.of(next);
}

const words = TaskReminderWords(dueTitle: 'Task due', leadTitle: _lead, untitled: 'Tasks');
String _lead(int minutes) => 'Due in $minutes min';

void main() {
  final now = DateTime(2026, 11, 26, 9);
  const settings = TaskReminderSettings(enabled: true, leadMinutes: 15);

  group('TaskReminderPlan', () {
    test('ids are stable, positive, and differ by kind', () {
      final due = TaskReminderPlan.notificationId('task-1', TaskReminderKind.due);
      final lead = TaskReminderPlan.notificationId('task-1', TaskReminderKind.lead);
      expect(due, TaskReminderPlan.notificationId('task-1', TaskReminderKind.due));
      expect(due, isNot(lead));
      expect(due, greaterThan(0));
      expect(lead, lessThan(1 << 31));
      expect(TaskReminderPlan.notificationId('task-2', TaskReminderKind.due), isNot(due));
    });

    test('an open task due later gets a due and a lead reminder', () {
      final plan = TaskReminderPlan.plan([task('a', dueAt: now.add(const Duration(hours: 2)))], settings, now: now);
      expect(plan.map((r) => r.kind), [TaskReminderKind.lead, TaskReminderKind.due]);
      expect(plan.first.fireAt, now.add(const Duration(hours: 1, minutes: 45)));
      expect(plan.last.fireAt, now.add(const Duration(hours: 2)));
    });

    test('done, waiting, undated and past tasks do not remind', () {
      final plan = TaskReminderPlan.plan([
        task('done', dueAt: now.add(const Duration(hours: 1)), completed: true),
        task('waiting', dueAt: now.add(const Duration(hours: 1)), approval: 'pending'),
        task('declined', dueAt: now.add(const Duration(hours: 1)), approval: 'declined'),
        task('undated'),
        task('past', dueAt: now.subtract(const Duration(minutes: 1))),
      ], settings, now: now);
      expect(plan, isEmpty);
    });

    test('a lead time already passed keeps only the due reminder', () {
      final plan =
          TaskReminderPlan.plan([task('soon', dueAt: now.add(const Duration(minutes: 5)))], settings, now: now);
      expect(plan.map((r) => r.kind), [TaskReminderKind.due]);
    });

    test('lead 0 means only the due reminder; disabled means none', () {
      final soon = [task('a', dueAt: now.add(const Duration(hours: 3)))];
      expect(TaskReminderPlan.plan(soon, const TaskReminderSettings(leadMinutes: 0), now: now), hasLength(1));
      expect(TaskReminderPlan.plan(soon, const TaskReminderSettings(enabled: false), now: now), isEmpty);
    });

    test('a date-only task reminds at the morning hour of its date', () {
      final plan = TaskReminderPlan.plan([
        task('d',
            dueAt: DateTime.utc(2026, 11, 27, 23, 59),
            due: const GeneratedTaskDue(kind: 'date', date: '2026-11-27', timezone: 'UTC')),
      ], const TaskReminderSettings(leadMinutes: 0), now: now);
      expect(plan.single.fireAt, DateTime(2026, 11, 27, TaskReminderPlan.dateOnlyHour));
    });

    test('keeps only the soonest reminders', () {
      final many = [
        for (var i = 0; i < 100; i++) task('t$i', dueAt: now.add(Duration(hours: i + 1))),
      ];
      final plan = TaskReminderPlan.plan(many, settings, now: now);
      expect(plan, hasLength(TaskReminderPlan.maxScheduled));
      expect(plan.first.taskId, 't0');
    });
  });

  group('TaskReminderScheduler', () {
    late FakePlatform platform;
    late MemoryLedger ledger;
    late TaskReminderScheduler scheduler;
    var clock = now;

    setUp(() {
      clock = now;
      platform = FakePlatform();
      ledger = MemoryLedger();
      scheduler = TaskReminderScheduler(platform: platform, ledger: ledger, now: () => clock);
    });

    Future<TaskReminderOutcome> reconcile(List<ActionItemWithMetadata> tasks,
            {bool complete = true, TaskReminderSettings s = settings}) =>
        scheduler.reconcile(tasks, settings: s, words: words, complete: complete);

    test('schedules both reminders with the localized titles, then keeps them', () async {
      final tasks = [task('a', dueAt: now.add(const Duration(hours: 2)))];
      final first = await reconcile(tasks);
      expect(first.scheduled, 2);
      final due = platform.scheduled[TaskReminderPlan.notificationId('a', TaskReminderKind.due)]!;
      final lead = platform.scheduled[TaskReminderPlan.notificationId('a', TaskReminderKind.lead)]!;
      expect(due.title, 'Task due');
      expect(lead.title, 'Due in 15 min');
      expect(due.body, 'Call the dentist');
      final second = await reconcile(tasks);
      expect(second.scheduled, 0);
      expect(second.kept, 2);
    });

    test('a moved due time reschedules', () async {
      await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)))]);
      final outcome = await reconcile([task('a', dueAt: now.add(const Duration(hours: 4)))]);
      expect(outcome.scheduled, 2);
      expect(platform.scheduled[TaskReminderPlan.notificationId('a', TaskReminderKind.due)]!.at,
          now.add(const Duration(hours: 4)));
    });

    test('completing a task cancels its reminders', () async {
      await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)))]);
      final outcome =
          await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)), completed: true)], complete: false);
      expect(outcome.cancelled, 2);
      expect(platform.scheduled, isEmpty);
      expect(ledger.entries, isEmpty);
    });

    test('a task missing from a partial page keeps its reminder; a complete list cancels it', () async {
      await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)))]);
      expect((await reconcile(const [], complete: false)).cancelled, 0);
      expect(platform.scheduled, hasLength(2));
      expect((await reconcile(const [], complete: true)).cancelled, 2);
    });

    test('forget cancels a deleted task wherever it was', () async {
      await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)))]);
      await scheduler.forget('a');
      expect(platform.scheduled, isEmpty);
      expect(ledger.entries, isEmpty);
    });

    test('turning reminders off cancels everything', () async {
      await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)))]);
      final outcome = await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)))],
          s: const TaskReminderSettings(enabled: false));
      expect(outcome.cancelled, 2);
      expect(platform.scheduled, isEmpty);
    });

    test('blocked notifications schedule nothing and say so', () async {
      platform.allowed = false;
      final outcome = await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)))]);
      expect(outcome.notificationsBlocked, isTrue);
      expect(platform.scheduled, isEmpty);
    });

    test('without the exact-alarm permission reminders fall back to inexact, and move to exact once granted', () async {
      platform.exact = false;
      final tasks = [task('a', dueAt: now.add(const Duration(hours: 2)))];
      await reconcile(tasks);
      expect(platform.scheduled.values.every((r) => !r.exact), isTrue);
      platform.exact = true;
      final outcome = await reconcile(tasks);
      expect(outcome.scheduled, 2);
      expect(platform.scheduled.values.every((r) => r.exact), isTrue);
    });

    test('a reminder that already fired is dropped from the ledger without a cancel', () async {
      await reconcile([task('a', dueAt: now.add(const Duration(minutes: 30)))]);
      clock = now.add(const Duration(hours: 1));
      platform.cancelled.clear();
      await reconcile(const [], complete: false);
      expect(platform.cancelled, isEmpty);
      expect(ledger.entries, isEmpty);
    });

    test('an untitled task shows the fallback body', () async {
      await reconcile([task('a', dueAt: now.add(const Duration(hours: 2)), description: '  ')],
          s: const TaskReminderSettings(leadMinutes: 0));
      expect(platform.scheduled.values.single.body, 'Tasks');
    });
  });

  group('agent receipts', () {
    test('task receipts from a reply are read once; other kinds are ignored', () {
      final seen = <String>{};
      final blocks = [
        {'type': 'editReceipt', 'receiptId': 'r1', 'targetKind': 'task', 'targetId': 't1', 'action': 'created'},
        {'type': 'edit_receipt', 'receipt_id': 'r2', 'target_kind': 'task', 'target_id': 't2', 'action': 'updated'},
        {'type': 'editReceipt', 'receiptId': 'r3', 'targetKind': 'memory', 'targetId': 'm1', 'action': 'created'},
        {'type': 'text', 'text': 'hello'},
      ];
      expect(TaskReminderCoordinator.agentTaskIds(blocks, seen), ['t1', 't2']);
      expect(TaskReminderCoordinator.agentTaskIds(blocks, seen), isEmpty);
    });

    test('a deleted task receipt cancels instead of re-reading', () {
      TaskReminders.reset();
      final ids = TaskReminderCoordinator.agentTaskIds([
        {'type': 'editReceipt', 'receiptId': 'r9', 'targetKind': 'task', 'targetId': 't9', 'action': 'deleted'},
      ], <String>{});
      expect(ids, isEmpty);
    });
  });

  group('PhoneAlarm', () {
    test('an alarm only for a due time within the next day', () {
      expect(PhoneAlarm.alarmTimeFor(now.add(const Duration(hours: 3, minutes: 5)), now: now), (hour: 12, minute: 5));
      expect(PhoneAlarm.alarmTimeFor(now.subtract(const Duration(minutes: 1)), now: now), isNull);
      expect(PhoneAlarm.alarmTimeFor(now.add(const Duration(hours: 25)), now: now), isNull);
    });

    test('out-of-range values never reach the clock app; other platforms say unsupported', () async {
      final android = PhoneAlarm(supported: true);
      expect((await android.setAlarm(hour: 24, minute: 0))['reason'], 'invalid_time');
      expect((await android.setTimer(seconds: 0))['reason'], 'invalid_length');
      expect((await PhoneAlarm(supported: false).setTimer(seconds: 60))['reason'], 'unsupported_platform');
    });
  });
}
