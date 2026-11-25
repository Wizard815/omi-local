import 'dart:convert';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/env/env.dart';

/// A Connections call's value, or why it failed (the typed [ApiProblem], never the server's text).
/// A 409 means the connection is off or has no address or token (Settings > Connections).
class ConnectionResult<T> {
  final T? value;
  final ApiProblem? problem;

  const ConnectionResult.ok(T this.value) : problem = null;

  const ConnectionResult.failed(this.problem) : value = null;

  bool get ok => value != null;
  int? get statusCode => problem?.statusCode;

  /// The target is off or not set up (the server answered 409).
  bool get notReady => problem?.statusCode == 409;
}

Future<ConnectionResult<T>> _call<T>(
  String method,
  String path,
  T Function(Map<String, dynamic>) parse, {
  Object? body,
}) async {
  final result = await executeApi<T>(
    request: ApiRequest(
      url: '${Env.apiBaseUrl}$path',
      method: method,
      headers: body == null ? const {} : const {'Content-Type': 'application/json'},
      body: body == null ? '' : jsonEncode(body),
    ),
    decode: (text) => parse(jsonDecode(text) as Map<String, dynamic>),
  );
  return switch (result) {
    ApiSuccess<T>(:final data) => ConnectionResult.ok(data),
    ApiFailure<T>(:final problem) => ConnectionResult.failed(problem),
  };
}

ConnectionsState _state(Map<String, dynamic> json) =>
    ConnectionsState.fromGenerated(wire.GeneratedOutboundIntegrationsResponse.fromJson(json));

/// Every Connections server call, as one object so widget tests can stand in for the server.
class ConnectionsApi {
  const ConnectionsApi();

  Future<ConnectionResult<ConnectionsState>> load() => _call('GET', 'v1/integrations/outbound', _state);

  /// [changes] are the API's own keys for one target; `token` '' clears the stored token.
  Future<ConnectionResult<ConnectionsState>> update(String target, Map<String, Object?> changes) =>
      _call('PUT', 'v1/integrations/outbound', _state, body: {target: changes});

  Future<ConnectionResult<({bool ok, String error, String detail})>> test(String target) => _call(
        'POST',
        'v1/integrations/outbound/$target/test',
        (json) {
          final g = wire.GeneratedOutboundTestResponse.fromJson(json);
          return (ok: g.ok, error: g.error, detail: g.detail);
        },
      );

  Future<ConnectionResult<OutboundPreview>> previewConversation(String conversationId) => _call(
        'GET',
        'v1/conversations/$conversationId/send/preview?target=wiki',
        (json) => OutboundPreview.fromGenerated(wire.GeneratedOutboundPreviewResponse.fromJson(json)),
      );

  /// Returns (ok, alreadySent, receipt, error).
  Future<ConnectionResult<({bool ok, bool alreadySent, OutboundReceipt? receipt, String error})>> sendConversation(
          String conversationId,
          {bool revised = false}) =>
      _call(
        'POST',
        'v1/conversations/$conversationId/send',
        (json) {
          final g = wire.GeneratedSendConversationResponse.fromJson(json);
          final r = g.receipt;
          return (
            ok: g.ok,
            alreadySent: g.alreadySent,
            receipt: r == null ? null : OutboundReceipt.fromGenerated(r),
            error: g.error
          );
        },
        body: {'target': 'wiki', 'revised': revised},
      );

  Future<ConnectionResult<AgentJobPreview>> previewAgentJob(
    String target,
    AgentJobContext context, {
    required String prompt,
    String? agentId,
    bool includeTranscript = false,
  }) =>
      _call(
        'POST',
        'v1/agent-jobs/preview',
        (json) => AgentJobPreview.fromGenerated(wire.GeneratedAgentJobPreviewResponse.fromJson(json)),
        body: {
          'target': target,
          'prompt': prompt,
          if (agentId != null) 'agent_id': agentId,
          'include_transcript': includeTranscript,
          ...context.toJson(),
        },
      );

  Future<ConnectionResult<AgentJob>> createAgentJob(
    String target,
    AgentJobContext context, {
    required String prompt,
    String? agentId,
    bool includeTranscript = false,
    bool attachResult = false,
  }) =>
      _call(
        'POST',
        'v1/agent-jobs',
        (json) {
          final g = wire.GeneratedAgentJobResponse.fromJson(json);
          final job = g.job;
          if (job == null) throw const FormatException('no job');
          return AgentJob.fromGenerated(job);
        },
        body: {
          'target': target,
          'prompt': prompt,
          if (agentId != null) 'agent_id': agentId,
          'include_transcript': includeTranscript,
          'attach_result': attachResult,
          ...context.toJson(),
        },
      );

  Future<ConnectionResult<List<AgentJob>>> listAgentJobs() => _call(
        'GET',
        'v1/agent-jobs',
        (json) =>
            (wire.GeneratedAgentJobsResponse.fromJson(json).jobs ?? const []).map(AgentJob.fromGenerated).toList(),
      );

  Future<ConnectionResult<AgentJob>> answerAgentJob(String jobId, String choice) => _call(
        'POST',
        'v1/agent-jobs/$jobId/approval',
        (json) {
          final job = wire.GeneratedAgentJobResponse.fromJson(json).job;
          if (job == null) throw const FormatException('no job');
          return AgentJob.fromGenerated(job);
        },
        body: {'choice': choice},
      );

  /// Studio's Agent work approvals in Omi's chats; `error` says why Studio could not be read.
  Future<ConnectionResult<({List<StudioApproval> approvals, String error})>> listStudioApprovals() => _call(
        'GET',
        'v1/agent-jobs/studio/approvals',
        (json) {
          final g = wire.GeneratedStudioApprovalsResponse.fromJson(json);
          return (
            approvals: (g.approvals ?? const []).map(StudioApproval.fromGenerated).toList(),
            error: g.error,
          );
        },
      );

  /// [decision] is 'approve' or 'reject'; [by] is the name Studio shows ("Robin (Omi)").
  Future<ConnectionResult<StudioApprovalAnswer>> answerStudioApproval(String workId, String decision, {String? by}) =>
      _call(
        'POST',
        'v1/agent-jobs/studio/approvals/${Uri.encodeComponent(workId)}',
        (json) => StudioApprovalAnswer.fromGenerated(wire.GeneratedStudioApprovalAnswerResponse.fromJson(json)),
        body: {'decision': decision, if (by != null && by.isNotEmpty) 'by': by},
      );

  Future<ConnectionResult<({String title, String body})>> coinpileDraft({String? conversationId, String? taskId}) {
    final query = [
      if (conversationId != null) 'conversation_id=${Uri.encodeQueryComponent(conversationId)}',
      if (taskId != null) 'task_id=${Uri.encodeQueryComponent(taskId)}',
    ].join('&');
    return _call(
      'GET',
      'v1/integrations/coinpile/draft?$query',
      (json) {
        final g = wire.GeneratedCoinpileSendRequest.fromJson(json);
        return (title: g.title, body: g.body);
      },
    );
  }

  Future<ConnectionResult<({List<CoinpileAccount> accounts, String error})>> coinpileAccounts() => _call(
        'GET',
        'v1/integrations/coinpile/accounts',
        (json) {
          final g = wire.GeneratedCoinpileAccountsResponse.fromJson(json);
          return (
            accounts: [
              for (final a in g.accounts ?? const <wire.GeneratedCoinpileAccount>[])
                CoinpileAccount(id: a.id, name: a.name, type: a.type)
            ],
            error: g.error,
          );
        },
      );

  Future<ConnectionResult<({bool ok, String answer, String error})>> coinpileSend(Map<String, Object?> request) =>
      _call(
        'POST',
        'v1/integrations/coinpile/send',
        (json) {
          final g = wire.GeneratedCoinpileSendResponse.fromJson(json);
          return (ok: g.ok, answer: g.answer, error: g.error);
        },
        body: request,
      );
}
