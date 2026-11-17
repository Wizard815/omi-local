// omi-local: the tasks Omi extracted from conversations that wait for the user's OK, and how many
// heard events and day summary drafts wait too. Owned by ActionItemsProvider (Tasks tab) and
// usable on its own (day review, banner). Approving or declining is a server call; a decline can
// be undone.
import 'package:flutter/foundation.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/action_item.dart';

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

  /// Waiting tasks of one local day (by creation), for the day review's "From this day".
  List<ActionItemWithMetadata> tasksOf(DateTime day) => tasks.where((t) {
        final created = t.createdAt?.toLocal();
        return created != null && created.year == day.year && created.month == day.month && created.day == day.day;
      }).toList();

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
    state = WaitingForApproval(
      tasks: [...state.tasks.where((t) => t.id != task.id), task],
      eventCount: state.eventCount,
      draftDates: state.draftDates,
      today: state.today,
      autoApprove: state.autoApprove,
      autoApproveTime: state.autoApproveTime,
    );
    notifyListeners();
    return true;
  }

  void _remove(String id) {
    state = WaitingForApproval(
      tasks: state.tasks.where((t) => t.id != id).toList(),
      eventCount: state.eventCount,
      draftDates: state.draftDates,
      today: state.today,
      autoApprove: state.autoApprove,
      autoApproveTime: state.autoApproveTime,
    );
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
