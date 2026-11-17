import 'dart:convert';

import 'package:omi/backend/http/shared.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/backend/schema/gen/misc_wire.g.dart' as misc;
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/env/env.dart';
import 'package:omi/utils/logger.dart';

/// A Connections call's value, or the server's message (e.g. "Hermes is off ... (Settings > Connections)").
class ConnectionResult<T> {
  final T? value;
  final String? error;
  final int? statusCode;

  const ConnectionResult.ok(T this.value)
      : error = null,
        statusCode = null;

  const ConnectionResult.failed(this.error, {this.statusCode}) : value = null;

  bool get ok => value != null;
}

ConnectionResult<T> _decode<T>(dynamic response, T Function(Map<String, dynamic>) parse, String label) {
  if (response == null) return const ConnectionResult.failed(null);
  final body = utf8.decode(response.bodyBytes);
  final int status = response.statusCode;
  if (status == 200) return ConnectionResult.ok(parse(jsonDecode(body) as Map<String, dynamic>));
  Logger.debug('$label failed: $status');
  try {
    final detail = misc.GeneratedErrorResponse.fromJson(jsonDecode(body) as Map<String, dynamic>).detail;
    return ConnectionResult.failed(detail is String ? detail : null, statusCode: status);
  } catch (_) {
    return ConnectionResult.failed(null, statusCode: status);
  }
}

Future<ConnectionResult<T>> _call<T>(
  String method,
  String path,
  T Function(Map<String, dynamic>) parse, {
  Object? body,
  String label = 'connections',
}) async {
  try {
    final response = await makeApiCall(
      url: '${Env.apiBaseUrl}$path',
      headers: body == null ? {} : {'Content-Type': 'application/json'},
      method: method,
      body: body == null ? '' : jsonEncode(body),
    );
    return _decode(response, parse, label);
  } catch (error) {
    // Not signed in yet, offline, or an unreadable answer: the page shows "could not load".
    Logger.debug('$label failed: ${error.runtimeType}');
    return const ConnectionResult.failed(null);
  }
}

ConnectionsState _state(Map<String, dynamic> json) =>
    ConnectionsState.fromGenerated(wire.GeneratedOutboundIntegrationsResponse.fromJson(json));

/// Every Connections server call, as one object so widget tests can stand in for the server.
class ConnectionsApi {
  const ConnectionsApi();

  Future<ConnectionResult<ConnectionsState>> load() =>
      _call('GET', 'v1/integrations/outbound', _state, label: 'getOutbound');

  /// [changes] are the API's own keys for one target; `token` '' clears the stored token.
  Future<ConnectionResult<ConnectionsState>> update(String target, Map<String, Object?> changes) =>
      _call('PUT', 'v1/integrations/outbound', _state, body: {target: changes}, label: 'updateOutbound');

  Future<ConnectionResult<({bool ok, String error, String detail})>> test(String target) => _call(
        'POST',
        'v1/integrations/outbound/$target/test',
        (json) {
          final g = wire.GeneratedOutboundTestResponse.fromJson(json);
          return (ok: g.ok, error: g.error, detail: g.detail);
        },
        label: 'testOutbound',
      );

  Future<ConnectionResult<OutboundPreview>> previewConversation(String conversationId) => _call(
        'GET',
        'v1/conversations/$conversationId/send/preview?target=wiki',
        (json) => OutboundPreview.fromGenerated(wire.GeneratedOutboundPreviewResponse.fromJson(json)),
        label: 'previewSend',
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
        label: 'sendConversation',
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
        label: 'previewAgentJob',
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
        label: 'createAgentJob',
      );

  Future<ConnectionResult<List<AgentJob>>> listAgentJobs() => _call(
        'GET',
        'v1/agent-jobs',
        (json) =>
            (wire.GeneratedAgentJobsResponse.fromJson(json).jobs ?? const []).map(AgentJob.fromGenerated).toList(),
        label: 'listAgentJobs',
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
        label: 'answerAgentJob',
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
      label: 'coinpileDraft',
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
        label: 'coinpileAccounts',
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
        label: 'coinpileSend',
      );
}
