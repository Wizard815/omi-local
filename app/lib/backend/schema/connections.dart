/// omi-local Connections: where Omi may send the user's things (wiki, Odysseus, Hermes, Studio,
/// Coinpile, Pondering Orb), what was sent, and the agent jobs. Mapped from the generated wire DTOs
/// (schema/gen/self_hosted_wire.g.dart); the server decides what may leave (utils/integrations_out.py).
library;

import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;

/// The six targets, in the order the Connections page shows them.
const connectionTargets = ['wiki', 'odysseus', 'hermes', 'studio', 'coinpile', 'orb'];

/// One target's settings. [options] holds the target's own choices (snake_case keys as the API
/// names them), e.g. the wiki's `send_day_reviews` or Odysseus' `endpoint_id`.
class ConnectionTarget {
  final String name;
  final bool enabled;
  final String url;
  final String ownUrl;
  final bool urlFromEnv;
  final bool hasToken;
  final bool tokenFromEnv;
  final bool? lastCheckOk;
  final String lastCheckError;
  final Map<String, Object?> options;

  const ConnectionTarget({
    required this.name,
    this.enabled = false,
    this.url = '',
    this.ownUrl = '',
    this.urlFromEnv = false,
    this.hasToken = false,
    this.tokenFromEnv = false,
    this.lastCheckOk,
    this.lastCheckError = '',
    this.options = const {},
  });

  /// On, with an address and a token: the server will send to it.
  bool get ready => enabled && url.isNotEmpty && hasToken;

  bool flag(String key, {bool fallback = false}) => (options[key] as bool?) ?? fallback;
  String text(String key) => (options[key] as String?) ?? '';
}

class ConnectionsState {
  final Map<String, ConnectionTarget> targets;

  /// The developer Conversation Events webhook points at the wiki Omi sends to.
  final bool conversationWebhookToWiki;

  const ConnectionsState({required this.targets, this.conversationWebhookToWiki = false});

  ConnectionTarget operator [](String name) => targets[name] ?? ConnectionTarget(name: name);

  static ConnectionTarget _base(
    String name, {
    required bool enabled,
    required String url,
    required String ownUrl,
    required bool urlFromEnv,
    required bool hasToken,
    required bool tokenFromEnv,
    required bool? lastCheckOk,
    required String lastCheckError,
    Map<String, Object?> options = const {},
  }) =>
      ConnectionTarget(
        name: name,
        enabled: enabled,
        url: url,
        ownUrl: ownUrl,
        urlFromEnv: urlFromEnv,
        hasToken: hasToken,
        tokenFromEnv: tokenFromEnv,
        lastCheckOk: lastCheckOk,
        lastCheckError: lastCheckError,
        options: options,
      );

  factory ConnectionsState.fromGenerated(wire.GeneratedOutboundIntegrationsResponse g) {
    final targets = <String, ConnectionTarget>{};
    final wiki = g.wiki;
    if (wiki != null) {
      targets['wiki'] = _base('wiki',
          enabled: wiki.enabled,
          url: wiki.url,
          ownUrl: wiki.ownUrl,
          urlFromEnv: wiki.urlFromEnv,
          hasToken: wiki.hasToken,
          tokenFromEnv: wiki.tokenFromEnv,
          lastCheckOk: wiki.lastCheckOk,
          lastCheckError: wiki.lastCheckError,
          options: {
            'person': wiki.person,
            'send_day_reviews': wiki.sendDayReviews,
            'send_kept_conversations': wiki.sendKeptConversations,
            'include_transcript': wiki.includeTranscript,
            'require_manual_approval': wiki.requireManualApproval,
          });
    }
    final hermes = g.hermes;
    if (hermes != null) {
      targets['hermes'] = _base('hermes',
          enabled: hermes.enabled,
          url: hermes.url,
          ownUrl: hermes.ownUrl,
          urlFromEnv: hermes.urlFromEnv,
          hasToken: hermes.hasToken,
          tokenFromEnv: hermes.tokenFromEnv,
          lastCheckOk: hermes.lastCheckOk,
          lastCheckError: hermes.lastCheckError);
    }
    final ody = g.odysseus;
    if (ody != null) {
      targets['odysseus'] = _base('odysseus',
          enabled: ody.enabled,
          url: ody.url,
          ownUrl: ody.ownUrl,
          urlFromEnv: ody.urlFromEnv,
          hasToken: ody.hasToken,
          tokenFromEnv: ody.tokenFromEnv,
          lastCheckOk: ody.lastCheckOk,
          lastCheckError: ody.lastCheckError,
          options: {'endpoint_id': ody.endpointId, 'model': ody.model, 'session_id': ody.sessionId});
    }
    final studio = g.studio;
    if (studio != null) {
      targets['studio'] = _base('studio',
          enabled: studio.enabled,
          url: studio.url,
          ownUrl: studio.ownUrl,
          urlFromEnv: studio.urlFromEnv,
          hasToken: studio.hasToken,
          tokenFromEnv: studio.tokenFromEnv,
          lastCheckOk: studio.lastCheckOk,
          lastCheckError: studio.lastCheckError,
          options: {
            'default_agent': studio.defaultAgent,
            'workspace': studio.workspace,
            'has_webhook_secret': studio.hasWebhookSecret,
            'webhook_path': studio.webhookPath,
          });
    }
    final coinpile = g.coinpile;
    if (coinpile != null) {
      targets['coinpile'] = _base('coinpile',
          enabled: coinpile.enabled,
          url: coinpile.url,
          ownUrl: coinpile.ownUrl,
          urlFromEnv: coinpile.urlFromEnv,
          hasToken: coinpile.hasToken,
          tokenFromEnv: coinpile.tokenFromEnv,
          lastCheckOk: coinpile.lastCheckOk,
          lastCheckError: coinpile.lastCheckError);
    }
    final orb = g.orb;
    if (orb != null) {
      targets['orb'] = _base('orb',
          enabled: orb.enabled,
          url: orb.url,
          ownUrl: orb.ownUrl,
          urlFromEnv: orb.urlFromEnv,
          hasToken: orb.hasToken,
          tokenFromEnv: orb.tokenFromEnv,
          lastCheckOk: orb.lastCheckOk,
          lastCheckError: orb.lastCheckError,
          options: {
            'log_day_approvals': orb.logDayApprovals,
            'project': orb.project,
            'require_manual_approval': orb.requireManualApproval,
          });
    }
    return ConnectionsState(targets: targets, conversationWebhookToWiki: g.conversationWebhookToWiki);
  }
}

/// A copy sent somewhere (or scheduled, or refused).
class OutboundReceipt {
  final String target;
  final DateTime? sentAt;
  final DateTime? scheduledAt;
  final String title;
  final int revision;
  final String detail;
  final String error;

  const OutboundReceipt({
    required this.target,
    this.sentAt,
    this.scheduledAt,
    this.title = '',
    this.revision = 0,
    this.detail = '',
    this.error = '',
  });

  bool get sent => sentAt != null;
  bool get scheduled => sentAt == null && scheduledAt != null && error.isEmpty;

  factory OutboundReceipt.fromGenerated(wire.GeneratedOutboundReceipt g) => OutboundReceipt(
        target: g.target,
        sentAt: g.sentAt?.toLocal(),
        scheduledAt: g.scheduledAt?.toLocal(),
        title: g.title,
        revision: g.revision,
        detail: g.detail,
        error: g.error,
      );
}

/// Exactly what a send would file, and the copies already sent.
class OutboundPreview {
  final String title;
  final String content;
  final String destination;
  final List<OutboundReceipt> receipts;

  const OutboundPreview({this.title = '', this.content = '', this.destination = '', this.receipts = const []});

  bool get alreadySent => receipts.any((r) => r.sent);

  factory OutboundPreview.fromGenerated(wire.GeneratedOutboundPreviewResponse g) => OutboundPreview(
        title: g.title,
        content: g.content,
        destination: g.destination,
        receipts: (g.receipts ?? const []).map(OutboundReceipt.fromGenerated).toList(),
      );
}

class AgentJobApproval {
  final String requestId;
  final String command;
  final String description;
  final List<String> choices;

  const AgentJobApproval({this.requestId = '', this.command = '', this.description = '', this.choices = const []});

  /// Hermes' questions are answered from Omi. A Studio chat's own question ("Allow once / Deny")
  /// only in Studio; Studio's Agent work approvals are a [StudioApproval] each, answered from Omi.
  bool get answerableHere => choices.contains('once');
}

/// What the user sent to an agent and what came back.
class AgentJob {
  final String id;
  final String target;
  final String title;
  final String prompt;
  final String status;
  final String result;
  final String error;
  final AgentJobApproval? approval;
  final List<String> conversationIds;
  final DateTime? createdAt;
  final DateTime? finishedAt;

  const AgentJob({
    required this.id,
    required this.target,
    this.title = '',
    this.prompt = '',
    this.status = 'queued',
    this.result = '',
    this.error = '',
    this.approval,
    this.conversationIds = const [],
    this.createdAt,
    this.finishedAt,
  });

  bool get waiting => status == 'waiting_for_approval';
  bool get finished => status == 'completed' || status == 'failed' || status == 'cancelled';

  static AgentJob fromWire(
    String id,
    String target, {
    required String title,
    required String prompt,
    required String status,
    required String result,
    required String error,
    required String? approvalRequestId,
    required String? approvalCommand,
    required String? approvalDescription,
    required List<String>? approvalChoices,
    required List<String>? conversationIds,
    required DateTime? createdAt,
    required DateTime? finishedAt,
  }) =>
      AgentJob(
        id: id,
        target: target,
        title: title,
        prompt: prompt,
        status: status,
        result: result,
        error: error,
        approval: approvalRequestId == null
            ? null
            : AgentJobApproval(
                requestId: approvalRequestId,
                command: approvalCommand ?? '',
                description: approvalDescription ?? '',
                choices: approvalChoices ?? const [],
              ),
        conversationIds: conversationIds ?? const [],
        createdAt: createdAt?.toLocal(),
        finishedAt: finishedAt?.toLocal(),
      );

  factory AgentJob.fromGenerated(wire.GeneratedAgentJob g) => fromWire(g.id, g.target,
      title: g.title,
      prompt: g.prompt,
      status: g.status,
      result: g.result,
      error: g.error,
      approvalRequestId: g.approval?.requestId,
      approvalCommand: g.approval?.command,
      approvalDescription: g.approval?.description,
      approvalChoices: g.approval?.choices,
      conversationIds: g.conversationIds,
      createdAt: g.createdAt,
      finishedAt: g.finishedAt);
}

/// Agent work in a Studio chat Omi started, waiting for an approval (Studio's Agent work).
class StudioApproval {
  final String id;
  final String title;
  final String agent;
  final String summary;
  final String jobTitle;
  final String decision;
  final String decidedVia;
  final String decidedBy;
  final DateTime? createdAt;

  const StudioApproval({
    required this.id,
    this.title = '',
    this.agent = '',
    this.summary = '',
    this.jobTitle = '',
    this.decision = '',
    this.decidedVia = '',
    this.decidedBy = '',
    this.createdAt,
  });

  factory StudioApproval.fromGenerated(wire.GeneratedStudioApproval g) => StudioApproval(
        id: g.id,
        title: g.title,
        agent: g.agent,
        summary: g.summary,
        jobTitle: g.jobTitle,
        decision: g.decision,
        decidedVia: g.decidedVia,
        decidedBy: g.decidedBy,
        createdAt: g.createdAt?.toLocal(),
      );
}

/// Studio's answer to Approve / Reject. [alreadyAnswered]: someone answered first (or the work
/// moved on), and [message] is Studio's own sentence saying who, e.g. "Already approved in Studio by Robin.".
class StudioApprovalAnswer {
  final bool ok;
  final bool alreadyAnswered;
  final String message;

  const StudioApprovalAnswer({required this.ok, this.alreadyAnswered = false, this.message = ''});

  factory StudioApprovalAnswer.fromGenerated(wire.GeneratedStudioApprovalAnswerResponse g) =>
      StudioApprovalAnswer(ok: g.ok, alreadyAnswered: g.alreadyAnswered, message: g.message);
}

/// What "Send to agent" would give the agent.
class AgentJobPreview {
  final String target;
  final String destination;
  final String content;
  final String suggestedTarget;
  final String title;

  const AgentJobPreview({
    required this.target,
    this.destination = '',
    this.content = '',
    this.suggestedTarget = 'odysseus',
    this.title = '',
  });

  factory AgentJobPreview.fromGenerated(wire.GeneratedAgentJobPreviewResponse g) => AgentJobPreview(
        target: g.target,
        destination: g.destination,
        content: g.content,
        suggestedTarget: g.suggestedTarget,
        title: g.title,
      );
}

/// What a job is about: one or more conversations, a day-review section, or a task.
class AgentJobContext {
  final List<String> conversationIds;
  final String? sectionDate;
  final String? sectionId;
  final String? taskId;

  const AgentJobContext({this.conversationIds = const [], this.sectionDate, this.sectionId, this.taskId});

  Map<String, Object?> toJson() => {
        'conversation_ids': conversationIds,
        if (sectionDate != null && sectionId != null) 'section': {'date': sectionDate, 'section_id': sectionId},
        if (taskId != null) 'task_id': taskId,
      };
}

class CoinpileAccount {
  final String id;
  final String name;
  final String type;

  const CoinpileAccount({required this.id, this.name = '', this.type = ''});
}
