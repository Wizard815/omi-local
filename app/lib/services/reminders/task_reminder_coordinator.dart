// Owns when task reminders are reconciled: on task-list and setting changes, on resume, and when
// a chat reply carries a task edit receipt. Scheduling itself stays in TaskReminderScheduler.

import 'dart:async';

import 'package:flutter/widgets.dart';

import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/backend/schema/message.dart';
import 'package:omi/services/reminders/task_reminder_scheduler.dart';
import 'package:omi/services/reminders/task_reminder_settings.dart';
import 'package:omi/utils/logger.dart';

/// The task list as the coordinator needs it; `ActionItemsProvider` in the app.
abstract class TaskReminderSource implements Listenable {
  bool get hasLoaded;

  /// Every task the app holds (the loaded pages plus today's).
  List<ActionItemWithMetadata> get reminderTasks;

  /// True when [reminderTasks] is the whole open list (no more pages, no filter or search), so a
  /// task missing from it is gone.
  bool get reminderTasksComplete;

  /// Re-reads one task the chat agent created or changed.
  Future<bool> receiveSavedTask(String id);
}

/// The process-wide reminder scheduler, so a completed or deleted task cancels its reminders from
/// wherever that happens. Inactive (a no-op) until the Home shell starts a coordinator.
abstract final class TaskReminders {
  static TaskReminderScheduler? _scheduler;

  static TaskReminderScheduler? get scheduler => _scheduler;

  static void activate(TaskReminderScheduler scheduler) => _scheduler = scheduler;

  @visibleForTesting
  static void reset() => _scheduler = null;

  static void forget(String taskId) {
    final scheduler = _scheduler;
    if (scheduler == null) return;
    unawaited(scheduler.forget(taskId).catchError((Object e) => Logger.debug('TaskReminders.forget: $e')));
  }

  static Future<void> cancelAll() async {
    try {
      await _scheduler?.cancelAll();
    } catch (e) {
      Logger.debug('TaskReminders.cancelAll: $e');
    }
  }
}

/// Keeps the phone's task reminders in step with the task list and the setting: a change to a
/// task (added, moved, completed, deleted) reschedules after a short pause. A task the chat agent
/// creates with a due time arrives as an edit receipt in the reply; the coordinator re-reads that
/// task so its reminder is scheduled without opening Tasks.
class TaskReminderCoordinator with WidgetsBindingObserver {
  TaskReminderCoordinator({
    required this.tasks,
    required this.scheduler,
    required this.words,
    TaskReminderSettingsStore? settings,
    this.messages,
    this.messageList,
    this.delay = const Duration(seconds: 1),
  }) : settings = settings ?? TaskReminderSettingsStore.instance;

  final TaskReminderSource tasks;
  final TaskReminderScheduler scheduler;
  final TaskReminderSettingsStore settings;
  final TaskReminderWords Function() words;

  /// The chat transcript, for agent-created tasks.
  final Listenable? messages;
  final List<ServerMessage> Function()? messageList;
  final Duration delay;

  Timer? _timer;
  final Set<String> _seenReceipts = {};
  int _lastMessageCount = -1;
  int _lastBlockCount = -1;

  void start() {
    TaskReminders.activate(scheduler);
    tasks.addListener(_schedule);
    settings.addListener(_schedule);
    messages?.addListener(_onMessages);
    WidgetsBinding.instance.addObserver(this);
    unawaited(settings.ensureLoaded().then((_) => _schedule()));
  }

  void dispose() {
    _timer?.cancel();
    tasks.removeListener(_schedule);
    settings.removeListener(_schedule);
    messages?.removeListener(_onMessages);
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Coming back: a permission may have been granted in system settings meanwhile.
    if (state == AppLifecycleState.resumed) _schedule();
  }

  void _schedule() {
    _timer?.cancel();
    _timer = Timer(delay, () => unawaited(reconcileNow()));
  }

  @visibleForTesting
  Future<TaskReminderOutcome?> reconcileNow() async {
    _timer?.cancel();
    if (!settings.loaded || !tasks.hasLoaded) return null;
    try {
      return await scheduler.reconcile(
        tasks.reminderTasks,
        settings: settings.value,
        words: words(),
        complete: tasks.reminderTasksComplete,
      );
    } catch (e) {
      Logger.debug('TaskReminderCoordinator.reconcile failed: $e');
      return null;
    }
  }

  /// Streaming notifies per token: only look when the last message gains a block.
  void _onMessages() {
    final list = messageList?.call();
    if (list == null || list.isEmpty) return;
    // The newest reply is at one end of the list; look at both so the order does not matter.
    final ends = {list.first, list.last}.where((m) => m.sender == MessageSender.ai).toList();
    final blocks = ends.fold<int>(0, (sum, m) => sum + m.contentBlocks.length);
    if (list.length == _lastMessageCount && blocks == _lastBlockCount) return;
    _lastMessageCount = list.length;
    _lastBlockCount = blocks;
    for (final message in ends) {
      for (final id in agentTaskIds(message.contentBlocks, _seenReceipts)) {
        unawaited(tasks.receiveSavedTask(id));
      }
    }
  }

  /// Task ids from edit receipts not seen before (created, updated, completed, uncompleted). Marks
  /// them seen.
  static List<String> agentTaskIds(List<Map<String, dynamic>> blocks, Set<String> seen) {
    final ids = <String>[];
    for (final block in blocks) {
      final type = block['type'];
      if (type != 'editReceipt' && type != 'edit_receipt') continue;
      if ((block['targetKind'] ?? block['target_kind']) != 'task') continue;
      final receipt = (block['receiptId'] ?? block['receipt_id'])?.toString();
      final target = (block['targetId'] ?? block['target_id'])?.toString();
      if (receipt == null || target == null || target.isEmpty || !seen.add(receipt)) continue;
      if (block['action'] == 'deleted') {
        TaskReminders.forget(target);
        continue;
      }
      ids.add(target);
    }
    return ids;
  }
}
