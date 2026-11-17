// omi-local: the tasks Omi extracted from conversations that wait for the user's OK, and how many
// heard events and day summary drafts wait too. Owned by ActionItemsProvider (Tasks tab) and
// usable on its own (day review, banner). Approving or declining is a server call; a decline can
// be undone, and so can Approve all / Approve selected (exactly the batch it approved).
import 'package:flutter/foundation.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/backend/schema/family_calendar.dart';

class WaitingApprovalController extends ChangeNotifier {
  WaitingApprovalController({required ApprovalsApi api, Future<void> Function()? onApproved})
      : _api = api,
        _onApproved = onApproved;

  final ApprovalsApi _api;
  final Future<void> Function()? _onApproved;
  WaitingForApproval state = const WaitingForApproval();
  bool loaded = false;
  bool failed = false;
  bool _disposed = false;
  final Set<String> _busy = {};

  List<ActionItemWithMetadata> get tasks => state.tasks;
  bool busy(String id) => _busy.contains(id);

  List<FamilyCalendarEvent> get events => state.events;
  List<String> get draftDates => state.draftDates;
  bool _batchBusy = false;
  bool get batchBusy => _batchBusy;

  static bool _sameDay(DateTime? a, DateTime day) =>
      a != null && a.year == day.year && a.month == day.month && a.day == day.day;

  static String dateKey(DateTime day) =>
      '${day.year.toString().padLeft(4, '0')}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';

  /// Waiting tasks of one local day (by creation), for the day review's "From this day".
  List<ActionItemWithMetadata> tasksOf(DateTime day) =>
      tasks.where((t) => _sameDay(t.createdAt?.toLocal(), day)).toList();

  /// Waiting events heard on one local day.
  List<FamilyCalendarEvent> eventsOf(DateTime day) => events.where((e) => _sameDay(e.heardAt, day)).toList();

  /// That day's summary draft, if it waits.
  List<String> draftsOf(DateTime day) => draftDates.where((d) => d == dateKey(day)).toList();

  /// Approve all (of one local [day]): everything waiting, approved at once. The batch it returns
  /// is what [undoBatch] takes back; null when the server refused.
  Future<ApprovalBatch?> approveAll({DateTime? day}) =>
      _runBatch(() => _api.approveAll(date: day == null ? null : dateKey(day)));

  /// Approve selected: exactly the items the user left ticked.
  Future<ApprovalBatch?> approveSelected(ApprovalBatch chosen) => _runBatch(() => _api.approveSelected(chosen));

  Future<ApprovalBatch?> _runBatch(Future<ApiResult<ApprovalBatch>> Function() call) async {
    if (_batchBusy) return null;
    _batchBusy = true;
    notifyListeners();
    try {
      final result = await call();
      if (_disposed || result is! ApiSuccess<ApprovalBatch>) return null;
      await load();
      if (result.data.taskIds.isNotEmpty) await _onApproved?.call();
      return result.data;
    } finally {
      _batchBusy = false;
      if (!_disposed) notifyListeners();
    }
  }

  /// Undo a batch: what it approved waits again (a day summary goes back to its draft as it was).
  Future<bool> undoBatch(ApprovalBatch batch) async {
    final result = await _api.undoBatch(batch);
    if (_disposed || result is! ApiSuccess) return false;
    await load();
    if (batch.taskIds.isNotEmpty) await _onApproved?.call();
    return true;
  }

  Future<void> load() async {
    final result = await _api.waiting();
    if (_disposed) return;
    switch (result) {
      case ApiSuccess(:final data):
        state = data;
        loaded = true;
        failed = false;
      case ApiFailure():
        failed = true;
    }
    notifyListeners();
  }

  /// Approve one task, optionally with the user's edits. True when the server took it.
  Future<bool> approve(ActionItemWithMetadata task, {String? description, DateTime? dueAt}) async {
    if (!_busy.add(task.id)) return false;
    notifyListeners();
    try {
      final result = await _api.approveTask(task.id, description: description, dueAt: dueAt);
      if (_disposed || result is! ApiSuccess) return false;
      _remove(task.id);
      await _onApproved?.call();
      return true;
    } finally {
      _busy.remove(task.id);
      if (!_disposed) notifyListeners();
    }
  }

  /// Decline one task (kept on the server as declined, so [undoDecline] can bring it back).
  Future<bool> decline(ActionItemWithMetadata task) async {
    if (!_busy.add(task.id)) return false;
    notifyListeners();
    try {
      final result = await _api.declineTask(task.id);
      if (_disposed || result is! ApiSuccess) return false;
      _remove(task.id);
      return true;
    } finally {
      _busy.remove(task.id);
      if (!_disposed) notifyListeners();
    }
  }

  Future<bool> undoDecline(ActionItemWithMetadata task) async {
    final result = await _api.declineTask(task.id, declined: false);
    if (_disposed || result is! ApiSuccess) return false;
    state = state.copyWith(tasks: [...state.tasks.where((t) => t.id != task.id), task]);
    notifyListeners();
    return true;
  }

  void _remove(String id) {
    state = state.copyWith(tasks: state.tasks.where((t) => t.id != id).toList());
  }

  void reset() {
    state = const WaitingForApproval();
    loaded = false;
    failed = false;
    _busy.clear();
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// Whether a task from the list is one the user still has to approve (or declined): such tasks
/// live only in the "Waiting for your OK" section, never in the normal list or reminders.
bool isAwaitingApproval(ActionItemWithMetadata task) => task.approval == 'pending' || task.approval == 'declined';
