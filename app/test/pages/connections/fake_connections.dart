import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';

/// A Connections server in memory: records every call, answers like the backend.
class FakeConnections extends ConnectionsApi {
  FakeConnections({ConnectionsState? state, this.jobs = const []})
      : state = state ??
            const ConnectionsState(targets: {
              'wiki': ConnectionTarget(name: 'wiki', options: {
                'send_day_reviews': true,
                'send_kept_conversations': 'off',
                'require_manual_approval': true
              }),
              'odysseus': ConnectionTarget(name: 'odysseus'),
              'hermes': ConnectionTarget(name: 'hermes'),
              'studio': ConnectionTarget(name: 'studio', options: {'default_agent': 'ekko-agent'}),
              'coinpile': ConnectionTarget(name: 'coinpile'),
              'orb': ConnectionTarget(name: 'orb', options: {'project': 'General', 'require_manual_approval': true}),
            });

  ConnectionsState state;
  List<AgentJob> jobs;
  final updates = <(String, Map<String, Object?>)>[];
  final tests = <String>[];
  final previews = <({String target, String prompt, bool transcript, Map<String, Object?> context})>[];
  final created = <({String target, String prompt, bool transcript, bool attach})>[];
  final answers = <(String, String)>[];
  final sends = <({String id, bool revised})>[];
  final coinpile = <Map<String, Object?>>[];
  OutboundPreview wikiPreview = const OutboundPreview(
    title: 'Conversation - Garage shelves (conv-1)',
    content: '**From:** Omi conversation\n\n## Overview\n\nPlanned shelves.',
    destination: 'http://192.168.1.5:27123 : raw/sources/2026-11-16 - Conversation - Garage shelves (conv-1).md',
  );

  @override
  Future<ConnectionResult<ConnectionsState>> load() async => ConnectionResult.ok(state);

  @override
  Future<ConnectionResult<ConnectionsState>> update(String target, Map<String, Object?> changes) async {
    updates.add((target, changes));
    final t = state[target];
    final options = {...t.options, for (final e in changes.entries) e.key: e.value};
    state = ConnectionsState(targets: {
      ...state.targets,
      target: ConnectionTarget(
        name: target,
        enabled: (changes['enabled'] as bool?) ?? t.enabled,
        url: (changes['url'] as String?) ?? t.url,
        ownUrl: (changes['url'] as String?) ?? t.ownUrl,
        hasToken: changes.containsKey('token') ? (changes['token'] as String).isNotEmpty : t.hasToken,
        options: options,
      ),
    });
    return ConnectionResult.ok(state);
  }

  @override
  Future<ConnectionResult<({bool ok, String error, String detail})>> test(String target) async {
    tests.add(target);
    return const ConnectionResult.ok((ok: true, error: '', detail: 'You are Wizard (member).'));
  }

  @override
  Future<ConnectionResult<AgentJobPreview>> previewAgentJob(String target, AgentJobContext context,
      {required String prompt, String? agentId, bool includeTranscript = false}) async {
    previews.add((target: target, prompt: prompt, transcript: includeTranscript, context: context.toJson()));
    return ConnectionResult.ok(AgentJobPreview(
      target: target,
      destination: target == 'hermes' ? 'http://192.168.1.9:8642' : 'http://192.168.1.9:7000',
      content: '$prompt\n\n---\nContext from Omi:\n\n## Conversation: Garage shelves',
      suggestedTarget: prompt.length > 40 ? 'hermes' : 'odysseus',
      title: 'Garage shelves',
    ));
  }

  @override
  Future<ConnectionResult<AgentJob>> createAgentJob(String target, AgentJobContext context,
      {required String prompt, String? agentId, bool includeTranscript = false, bool attachResult = false}) async {
    created.add((target: target, prompt: prompt, transcript: includeTranscript, attach: attachResult));
    return ConnectionResult.ok(AgentJob(id: 'job-1', target: target, title: 'Garage shelves', status: 'running'));
  }

  @override
  Future<ConnectionResult<List<AgentJob>>> listAgentJobs() async => ConnectionResult.ok(jobs);

  @override
  Future<ConnectionResult<AgentJob>> answerAgentJob(String jobId, String choice) async {
    answers.add((jobId, choice));
    jobs = [
      for (final j in jobs) j.id == jobId ? AgentJob(id: j.id, target: j.target, title: j.title, status: 'running') : j
    ];
    return ConnectionResult.ok(jobs.firstWhere((j) => j.id == jobId));
  }

  @override
  Future<ConnectionResult<OutboundPreview>> previewConversation(String conversationId) async =>
      ConnectionResult.ok(wikiPreview);

  @override
  Future<ConnectionResult<({bool ok, bool alreadySent, OutboundReceipt? receipt, String error})>> sendConversation(
      String conversationId,
      {bool revised = false}) async {
    sends.add((id: conversationId, revised: revised));
    return ConnectionResult.ok((
      ok: true,
      alreadySent: false,
      receipt: OutboundReceipt(target: 'wiki', sentAt: DateTime(2026, 11, 17), title: wikiPreview.title),
      error: ''
    ));
  }

  @override
  Future<ConnectionResult<({String title, String body})>> coinpileDraft(
          {String? conversationId, String? taskId}) async =>
      const ConnectionResult.ok((title: 'Plumber', body: 'Paid the plumber \$120 cash.'));

  @override
  Future<ConnectionResult<({List<CoinpileAccount> accounts, String error})>> coinpileAccounts() async =>
      const ConnectionResult.ok((accounts: [CoinpileAccount(id: 'ff:1', name: 'Cash', type: 'cash')], error: ''));

  @override
  Future<ConnectionResult<({bool ok, String answer, String error})>> coinpileSend(Map<String, Object?> request) async {
    coinpile.add(request);
    return const ConnectionResult.ok((ok: true, answer: 'asked', error: ''));
  }
}
