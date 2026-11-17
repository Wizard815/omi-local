// omi-local: what waits for the user's OK (tasks extracted from conversations, heard events,
// day summary drafts), Approve all / Approve selected with its Undo, and the optional
// auto-approve. Backend: routers/approvals.py.
import 'dart:convert';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/backend/schema/gen/action_items_folders_wire.g.dart' as wire;
import 'package:omi/env/env.dart';

/// Everything that waits, counted for the banner; the waiting tasks themselves.
class WaitingForApproval {
  final List<ActionItemWithMetadata> tasks;
  final int eventCount;

  /// The waiting events themselves (servers before Approve all send only [eventCount]).
  final List<FamilyCalendarEvent> events;
  final List<String> draftDates;
  final String today;
  final bool autoApprove;
  final String autoApproveTime;

  const WaitingForApproval({
    this.tasks = const [],
    this.eventCount = 0,
    this.events = const [],
    this.draftDates = const [],
    this.today = '',
    this.autoApprove = false,
    this.autoApproveTime = '',
  });

  int get taskCount => tasks.length;
  bool get isEmpty => tasks.isEmpty && eventCount == 0 && draftDates.isEmpty;

  WaitingForApproval copyWith({List<ActionItemWithMetadata>? tasks}) => WaitingForApproval(
        tasks: tasks ?? this.tasks,
        eventCount: eventCount,
        events: events,
        draftDates: draftDates,
        today: today,
        autoApprove: autoApprove,
        autoApproveTime: autoApproveTime,
      );

  factory WaitingForApproval.fromGenerated(wire.GeneratedWaitingForApprovalResponse g) => WaitingForApproval(
        tasks: g.tasks ?? const [],
        eventCount: g.eventCount,
        events: [for (final e in g.events ?? const <wire.GeneratedFamilyCalendarEvent>[]) _event(e)],
        draftDates: g.draftDates ?? const [],
        today: g.today,
        autoApprove: g.autoApprove,
        autoApproveTime: g.autoApproveTime,
      );
}

FamilyCalendarEvent _event(wire.GeneratedFamilyCalendarEvent g) => FamilyCalendarEvent(
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

/// An event in a batch: which conversation, and which of its events.
typedef ApprovalEventRef = ({String conversationId, int eventIdx});

/// What one Approve all / Approve selected approved: exactly what its Undo takes back.
class ApprovalBatch {
  final List<String> taskIds;
  final List<ApprovalEventRef> events;
  final List<String> dayDates;

  /// Items the server could not approve now (they still wait).
  final int failedCount;

  const ApprovalBatch({
    this.taskIds = const [],
    this.events = const [],
    this.dayDates = const [],
    this.failedCount = 0,
  });

  int get count => taskIds.length + events.length + dayDates.length;
  bool get isEmpty => count == 0;

  factory ApprovalBatch.fromGenerated(wire.GeneratedApproveBatchResponse g) => ApprovalBatch(
        taskIds: g.taskIds ?? const [],
        events: [
          for (final e in g.events ?? const <wire.GeneratedApprovalEventRef>[])
            (conversationId: e.conversationId, eventIdx: e.eventIdx)
        ],
        dayDates: g.dayDates ?? const [],
        failedCount: g.failedEventCount + (g.failedDayDates?.length ?? 0),
      );

  Map<String, dynamic> toJson() => {
        'task_ids': taskIds,
        'events': [
          for (final e in events) {'conversation_id': e.conversationId, 'event_idx': e.eventIdx}
        ],
        'day_dates': dayDates,
      };
}

/// The user's auto-approve choice. Off unless they turn it on.
class ApprovalSettings {
  final bool autoApprove;
  final String time;
  final bool operatorDisabled;

  const ApprovalSettings({this.autoApprove = false, this.time = '21:00', this.operatorDisabled = false});

  factory ApprovalSettings.fromGenerated(wire.GeneratedApprovalSettings g) =>
      ApprovalSettings(autoApprove: g.autoApprove, time: g.time, operatorDisabled: g.operatorDisabled);
}

/// The server calls the approval surfaces make; tests pass a fake.
class ApprovalsApi {
  /// [baseUrl] null = the app's configured backend; [send] is the transport seam tests use.
  const ApprovalsApi({String? baseUrl, ApiSend? send})
      : _baseUrl = baseUrl,
        _send = send;

  final String? _baseUrl;
  final ApiSend? _send;

  String get _base {
    final base = _baseUrl ?? Env.apiBaseUrl ?? '';
    return base.endsWith('/') ? base : '$base/';
  }

  Future<ApiResult<WaitingForApproval>> waiting() => executeApi(
        request: ApiRequest(url: '${_base}v1/approvals', method: 'GET'),
        send: _send,
        decode: (body) => WaitingForApproval.fromGenerated(
            wire.GeneratedWaitingForApprovalResponse.fromJson(jsonDecode(body) as Map<String, dynamic>)),
      );

  /// The user's yes, with their edits ([description], [dueAt] null = keep).
  Future<ApiResult<ActionItemWithMetadata?>> approveTask(String id, {String? description, DateTime? dueAt}) =>
      executeApi(
        request: ApiRequest(
          url: '${_base}v1/approvals/tasks/$id/approve',
          method: 'POST',
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({
            if (description != null) 'description': description,
            if (dueAt != null) 'due_at': dueAt.toUtc().toIso8601String(),
          }),
        ),
        send: _send,
        decode: _task,
      );

  /// The user's no ([declined] false takes it back: the task waits again).
  Future<ApiResult<ActionItemWithMetadata?>> declineTask(String id, {bool declined = true}) => executeApi(
        request: ApiRequest(
          url: '${_base}v1/approvals/tasks/$id/decline',
          method: 'POST',
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({'declined': declined}),
        ),
        send: _send,
        decode: _task,
      );

  /// Approve everything waiting ([date]: only what is of that local day), as the tap on Approve all.
  Future<ApiResult<ApprovalBatch>> approveAll({String? date}) =>
      _batch({'all_waiting': true, if (date != null) 'date': date});

  /// Approve exactly these (the items left ticked in Approve selected).
  Future<ApiResult<ApprovalBatch>> approveSelected(ApprovalBatch chosen) => _batch(chosen.toJson());

  Future<ApiResult<ApprovalBatch>> _batch(Map<String, dynamic> body) => executeApi(
        request: ApiRequest(
          url: '${_base}v1/approvals/batch',
          method: 'POST',
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode(body),
        ),
        send: _send,
        decode: (body) => ApprovalBatch.fromGenerated(
            wire.GeneratedApproveBatchResponse.fromJson(jsonDecode(body) as Map<String, dynamic>)),
      );

  /// Undo a batch: what it approved waits again (a day goes back to its draft as it was).
  Future<ApiResult<int>> undoBatch(ApprovalBatch batch) => executeApi(
        request: ApiRequest(
          url: '${_base}v1/approvals/batch/undo',
          method: 'POST',
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode(batch.toJson()),
        ),
        send: _send,
        decode: (body) {
          final g = wire.GeneratedUndoApproveBatchResponse.fromJson(jsonDecode(body) as Map<String, dynamic>);
          return g.taskCount + g.eventCount + g.dayCount;
        },
      );

  Future<ApiResult<ApprovalSettings>> settings() => executeApi(
        request: ApiRequest(url: '${_base}v1/approvals/settings', method: 'GET'),
        send: _send,
        decode: _settings,
      );

  Future<ApiResult<ApprovalSettings>> updateSettings({bool? autoApprove, String? time}) => executeApi(
        request: ApiRequest(
          url: '${_base}v1/approvals/settings',
          method: 'PUT',
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({
            if (autoApprove != null) 'auto_approve': autoApprove,
            if (time != null) 'time': time,
          }),
        ),
        send: _send,
        decode: _settings,
      );

  static ActionItemWithMetadata? _task(String body) {
    final g = wire.GeneratedTaskApprovalResponse.fromJson(jsonDecode(body) as Map<String, dynamic>);
    if (!g.ok) throw StateError(g.error);
    return g.task;
  }

  static ApprovalSettings _settings(String body) =>
      ApprovalSettings.fromGenerated(wire.GeneratedApprovalSettings.fromJson(jsonDecode(body) as Map<String, dynamic>));
}
