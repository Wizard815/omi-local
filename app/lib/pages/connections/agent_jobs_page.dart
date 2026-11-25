import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/pages/connections/connections_page.dart' show connectionProblem;
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// omi-local: what the user sent to an agent (Odysseus, Hermes, Studio), its status, the agent's
/// question when it asks before going on (Hermes: Allow once / Deny here; Studio: in Studio), and
/// the result.
class AgentJobsPage extends StatefulWidget {
  const AgentJobsPage({super.key, this.api = const ConnectionsApi()});

  final ConnectionsApi api;

  @override
  State<AgentJobsPage> createState() => _AgentJobsPageState();
}

class _AgentJobsPageState extends State<AgentJobsPage> {
  List<AgentJob>? _jobs;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final result = await widget.api.listAgentJobs();
    if (!mounted) return;
    setState(() {
      _jobs = result.value ?? _jobs ?? const [];
      _error = result.ok ? null : connectionProblem(context, result, context.l10n.agentJobsLoadFailed);
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final jobs = _jobs;
    return Scaffold(
      backgroundColor: OmiColors.surface0,
      appBar:
          AppBar(backgroundColor: OmiColors.surface0, leading: const OmiBackButton(), title: Text(l10n.agentJobsTitle)),
      body: jobs == null
          ? const Center(child: OmiSpinner())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                key: const Key('agent_jobs_list'),
                padding: const EdgeInsets.all(OmiSpacing.md),
                children: [
                  if (_error != null) Text(_error!, style: OmiType.footnote.copyWith(color: OmiColors.danger)),
                  if (jobs.isEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: OmiSpacing.xl),
                      child: Text(l10n.agentJobsEmpty,
                          textAlign: TextAlign.center, style: OmiType.body.copyWith(color: OmiColors.textSecondary)),
                    ),
                  for (final job in jobs) AgentJobCard(job: job, api: widget.api, onChanged: _load),
                ],
              ),
            ),
    );
  }
}

/// One job: agent, title, status, the agent's question (with answers where Omi can give them) and the result.
class AgentJobCard extends StatefulWidget {
  const AgentJobCard({super.key, required this.job, required this.api, required this.onChanged});

  final AgentJob job;
  final ConnectionsApi api;
  final Future<void> Function() onChanged;

  @override
  State<AgentJobCard> createState() => _AgentJobCardState();
}

class _AgentJobCardState extends State<AgentJobCard> {
  bool _busy = false;

  Future<void> _answer(String choice) async {
    setState(() => _busy = true);
    final result = await widget.api.answerAgentJob(widget.job.id, choice);
    if (!mounted) return;
    setState(() => _busy = false);
    if (!result.ok) {
      AppSnackbar.showSnackbarError(connectionProblem(context, result, context.l10n.agentJobsAnswerFailed));
    }
    await widget.onChanged();
  }

  String _agent(String target) => switch (target) {
        'hermes' => context.l10n.connectionsHermesTitle,
        'studio' => context.l10n.connectionsStudioTitle,
        _ => context.l10n.connectionsOdysseusTitle,
      };

  (String, Color) _status(AgentJob job) {
    final l10n = context.l10n;
    return switch (job.status) {
      'completed' => (l10n.agentJobsStatusDone, OmiColors.success),
      'failed' || 'cancelled' => (l10n.agentJobsStatusFailed, OmiColors.danger),
      'waiting_for_approval' => (l10n.agentJobsStatusAsking, OmiColors.warning),
      'running' => (l10n.agentJobsStatusRunning, OmiColors.textSecondary),
      _ => (l10n.agentJobsStatusQueued, OmiColors.textTertiary),
    };
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final job = widget.job;
    final (label, color) = _status(job);
    final approval = job.approval;
    return Padding(
      key: Key('agent_job_${job.id}'),
      padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
      child: Material(
        color: OmiColors.surface1,
        borderRadius: OmiRadius.lgAll,
        clipBehavior: Clip.antiAlias,
        child: Padding(
          padding: const EdgeInsets.all(OmiSpacing.sm),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                // Agent and time wrap under each other before they push the status off the card.
                Expanded(
                  child: Wrap(spacing: OmiSpacing.xs, children: [
                    Text(_agent(job.target), style: OmiType.caption.copyWith(color: OmiColors.textSecondary)),
                    if (job.createdAt != null)
                      Text(OmiDateFormat.of(context).timestamp(job.createdAt!.toLocal()),
                          style: OmiType.caption.copyWith(color: OmiColors.textTertiary)),
                  ]),
                ),
                const SizedBox(width: OmiSpacing.xs),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.xs, vertical: 2),
                  decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: OmiRadius.mdAll),
                  child: Text(label, style: OmiType.caption.copyWith(color: color)),
                ),
              ]),
              const SizedBox(height: OmiSpacing.xxs),
              Text(job.title.isEmpty ? l10n.agentJobsUntitled : job.title,
                  style: OmiType.headline.copyWith(color: OmiColors.textPrimary)),
              if (job.waiting && approval != null) ...[
                const SizedBox(height: OmiSpacing.xs),
                Text(l10n.agentJobsAsks(_agent(job.target)), style: OmiType.subhead.copyWith(color: OmiColors.warning)),
                if (approval.description.isNotEmpty)
                  Text(approval.description, style: OmiType.footnote.copyWith(color: OmiColors.textPrimary)),
                if (approval.command.isNotEmpty)
                  Container(
                    margin: const EdgeInsets.only(top: OmiSpacing.xxs),
                    padding: const EdgeInsets.all(OmiSpacing.xs),
                    decoration: BoxDecoration(color: OmiColors.surface2, borderRadius: OmiRadius.mdAll),
                    child: SelectableText(approval.command,
                        style: OmiType.footnote.copyWith(color: OmiColors.textPrimary, fontFamily: 'monospace')),
                  ),
                const SizedBox(height: OmiSpacing.xs),
                if (approval.answerableHere)
                  OmiButtonPair(
                    first: OmiButton(
                      key: Key('agent_job_allow_${job.id}'),
                      label: l10n.agentJobsAllowOnce,
                      onPressed: _busy ? null : () => _answer('once'),
                    ),
                    second: OmiButton.secondary(
                      key: Key('agent_job_deny_${job.id}'),
                      label: l10n.agentJobsDeny,
                      onPressed: _busy ? null : () => _answer('deny'),
                    ),
                  )
                else
                  Text(l10n.agentJobsAnswerInStudio, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
              ],
              if (job.result.isNotEmpty) ...[
                const SizedBox(height: OmiSpacing.xs),
                SelectableText(job.result,
                    key: Key('agent_job_result_${job.id}'), style: OmiType.body.copyWith(color: OmiColors.textPrimary)),
              ],
              if (job.error.isNotEmpty && !job.waiting) ...[
                const SizedBox(height: OmiSpacing.xs),
                Text(job.error, style: OmiType.footnote.copyWith(color: OmiColors.danger)),
              ],
              Theme(
                data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
                child: ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title:
                      Text(l10n.agentJobsWhatWasSent, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                  children: [
                    SelectableText(job.prompt,
                        style: OmiType.footnote.copyWith(color: OmiColors.textSecondary, fontFamily: 'monospace')),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
