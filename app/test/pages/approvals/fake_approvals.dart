// A stand-in for the approvals server (routers/approvals.py) for widget tests.
import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/backend/schema/family_calendar.dart';

ActionItemWithMetadata waitingTask(String id, String text, {DateTime? created}) => ActionItemWithMetadata(
      id: id,
      description: text,
      completed: false,
      approval: 'pending',
      createdAt: created ?? DateTime(2026, 11, 15, 9),
    );

FamilyCalendarEvent heardEvent(String conversationId, String title, {DateTime? heard, int idx = 0}) =>
    FamilyCalendarEvent(
      conversationId: conversationId,
      eventIdx: idx,
      conversationTitle: '',
      title: title,
      description: '',
      start: DateTime(2026, 11, 19, 15),
      duration: 30,
      heardAt: heard ?? DateTime(2026, 11, 15, 11),
    );

class FakeApprovals extends ApprovalsApi {
  FakeApprovals({
    List<ActionItemWithMetadata> tasks = const [],
    List<FamilyCalendarEvent> events = const [],
    int? eventCount,
    List<String> draftDates = const [],
    this.settingsValue = const ApprovalSettings(),
  })  : tasks = [...tasks],
        events = [...events],
        _eventCount = eventCount,
        draftDates = [...draftDates];

  final List<ActionItemWithMetadata> tasks;
  final List<FamilyCalendarEvent> events;
  final int? _eventCount;
  final List<String> draftDates;
  int get eventCount => _eventCount ?? events.length;

  /// What each Approve all / Approve selected sent: `all` (with its date) or the chosen batch.
  final batches = <({bool all, String? date, ApprovalBatch? chosen})>[];
  final undone = <ApprovalBatch>[];
  final _approvedTasks = <ActionItemWithMetadata>[];
  final _approvedEvents = <FamilyCalendarEvent>[];
  ApprovalSettings settingsValue;
  final approved = <({String id, String? description})>[];
  final declined = <({String id, bool declined})>[];

  @override
  Future<ApiResult<WaitingForApproval>> waiting() async => ApiSuccess(WaitingForApproval(
        tasks: [...tasks],
        eventCount: eventCount,
        events: [...events],
        draftDates: [...draftDates],
        today: '2026-11-16',
        autoApprove: settingsValue.autoApprove,
        autoApproveTime: settingsValue.time,
      ));

  @override
  Future<ApiResult<ActionItemWithMetadata?>> approveTask(String id, {String? description, DateTime? dueAt}) async {
    approved.add((id: id, description: description));
    tasks.removeWhere((t) => t.id == id);
    return const ApiSuccess(null);
  }

  @override
  Future<ApiResult<ActionItemWithMetadata?>> declineTask(String id, {bool declined = true}) async {
    this.declined.add((id: id, declined: declined));
    return const ApiSuccess(null);
  }

  bool _ofDay(DateTime? at, String? date) =>
      date == null ||
      (at != null &&
          '${at.year.toString().padLeft(4, '0')}-${at.month.toString().padLeft(2, '0')}-${at.day.toString().padLeft(2, '0')}' ==
              date);

  ApprovalBatch _approve(ApprovalBatch chosen) {
    final taskIds = [
      for (final t in tasks)
        if (chosen.taskIds.contains(t.id)) t.id
    ];
    final evs = [
      for (final e in events)
        if (chosen.events.any((r) => r.conversationId == e.conversationId && r.eventIdx == e.eventIdx)) e
    ];
    final days = [
      for (final d in draftDates)
        if (chosen.dayDates.contains(d)) d
    ];
    _approvedTasks.addAll(tasks.where((t) => taskIds.contains(t.id)));
    _approvedEvents.addAll(evs);
    tasks.removeWhere((t) => taskIds.contains(t.id));
    events.removeWhere(evs.contains);
    draftDates.removeWhere(days.contains);
    return ApprovalBatch(
      taskIds: taskIds,
      events: [for (final e in evs) (conversationId: e.conversationId, eventIdx: e.eventIdx)],
      dayDates: days,
    );
  }

  @override
  Future<ApiResult<ApprovalBatch>> approveAll({String? date}) async {
    batches.add((all: true, date: date, chosen: null));
    return ApiSuccess(_approve(ApprovalBatch(
      taskIds: [
        for (final t in tasks)
          if (_ofDay(t.createdAt, date)) t.id
      ],
      events: [
        for (final e in events)
          if (_ofDay(e.heardAt, date)) (conversationId: e.conversationId, eventIdx: e.eventIdx)
      ],
      dayDates: [
        for (final d in draftDates)
          if (date == null || d == date) d
      ],
    )));
  }

  @override
  Future<ApiResult<ApprovalBatch>> approveSelected(ApprovalBatch chosen) async {
    batches.add((all: false, date: null, chosen: chosen));
    return ApiSuccess(_approve(chosen));
  }

  @override
  Future<ApiResult<int>> undoBatch(ApprovalBatch batch) async {
    undone.add(batch);
    tasks.addAll(_approvedTasks.where((t) => batch.taskIds.contains(t.id)));
    events.addAll(_approvedEvents
        .where((e) => batch.events.any((r) => r.conversationId == e.conversationId && r.eventIdx == e.eventIdx)));
    draftDates.addAll(batch.dayDates);
    return ApiSuccess(batch.count);
  }

  @override
  Future<ApiResult<ApprovalSettings>> settings() async => ApiSuccess(settingsValue);

  @override
  Future<ApiResult<ApprovalSettings>> updateSettings({bool? autoApprove, String? time}) async {
    settingsValue = ApprovalSettings(
      autoApprove: autoApprove ?? settingsValue.autoApprove,
      time: time ?? settingsValue.time,
    );
    return ApiSuccess(settingsValue);
  }
}
