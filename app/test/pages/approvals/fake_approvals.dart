// A stand-in for the approvals server (routers/approvals.py) for widget tests.
import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/action_item.dart';

ActionItemWithMetadata waitingTask(String id, String text, {DateTime? created}) => ActionItemWithMetadata(
      id: id,
      description: text,
      completed: false,
      approval: 'pending',
      createdAt: created ?? DateTime(2026, 11, 15, 9),
    );

class FakeApprovals extends ApprovalsApi {
  FakeApprovals({
    List<ActionItemWithMetadata> tasks = const [],
    this.eventCount = 0,
    this.draftDates = const [],
    this.settingsValue = const ApprovalSettings(),
  }) : tasks = [...tasks];

  final List<ActionItemWithMetadata> tasks;
  final int eventCount;
  final List<String> draftDates;
  ApprovalSettings settingsValue;
  final approved = <({String id, String? description})>[];
  final declined = <({String id, bool declined})>[];

  @override
  Future<ApiResult<WaitingForApproval>> waiting() async => ApiSuccess(WaitingForApproval(
        tasks: [...tasks],
        eventCount: eventCount,
        draftDates: draftDates,
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
