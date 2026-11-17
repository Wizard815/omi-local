import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/pages/connections/agent_jobs_page.dart';
import 'package:omi/pages/connections/connections_page.dart' show studioAgents;
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// "Send to agent…" for a conversation, a day-review section or a task.
///
/// The user picks the agent (Odysseus for a simple ask, Hermes for a job, Studio for coding),
/// edits the prompt, and sees exactly what would leave Omi (the server's preview: the prompt and
/// the context it adds). Send is enabled only for the preview on screen: changing the prompt, the
/// agent or the transcript choice asks for a new preview first. That preview is the approval gate.
Future<AgentJob?> showSendToAgentSheet(
  BuildContext context, {
  required AgentJobContext jobContext,
  String initialPrompt = '',
  ConnectionsApi api = const ConnectionsApi(),
}) {
  return showOmiSheet<AgentJob>(
    context: context,
    title: context.l10n.sendToAgentTitle,
    builder: (_) => SendToAgentSheet(jobContext: jobContext, initialPrompt: initialPrompt, api: api),
  ).then((job) {
    if (job != null && context.mounted) {
      AppSnackbar.showSnackbar(context.l10n.sendToAgentSent(_agentName(context, job.target)));
    }
    return job;
  });
}

String _agentName(BuildContext context, String target) => switch (target) {
      'hermes' => context.l10n.connectionsHermesTitle,
      'studio' => context.l10n.connectionsStudioTitle,
      _ => context.l10n.connectionsOdysseusTitle,
    };

class SendToAgentSheet extends StatefulWidget {
  const SendToAgentSheet({super.key, required this.jobContext, this.initialPrompt = '', required this.api});

  final AgentJobContext jobContext;
  final String initialPrompt;
  final ConnectionsApi api;

  @override
  State<SendToAgentSheet> createState() => _SendToAgentSheetState();
}

class _SendToAgentSheetState extends State<SendToAgentSheet> {
  late final _prompt = TextEditingController(text: widget.initialPrompt);
  String _target = 'odysseus';
  String _studioAgent = 'ekko-agent';
  bool _includeTranscript = false;
  bool _attachResult = true;
  AgentJobPreview? _preview;

  /// What the preview on screen was made for; Send needs it to match the form.
  String? _previewKey;
  String? _error;
  bool _busy = false;

  bool get _hasConversation => widget.jobContext.conversationIds.isNotEmpty;

  String get _formKey => [_target, _studioAgent, _includeTranscript, _prompt.text.trim()].join('\u0000');

  @override
  void initState() {
    super.initState();
    _prompt.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _prompt.dispose();
    super.dispose();
  }

  Future<void> _showPreview() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final key = _formKey;
    final result = await widget.api.previewAgentJob(
      _target,
      widget.jobContext,
      prompt: _prompt.text.trim(),
      agentId: _target == 'studio' ? _studioAgent : null,
      includeTranscript: _includeTranscript,
    );
    if (!mounted) return;
    setState(() {
      _busy = false;
      _preview = result.value;
      _previewKey = result.ok ? key : null;
      _error = result.ok ? null : (result.error ?? context.l10n.sendToAgentPreviewFailed);
    });
  }

  Future<void> _send() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final result = await widget.api.createAgentJob(
      _target,
      widget.jobContext,
      prompt: _prompt.text.trim(),
      agentId: _target == 'studio' ? _studioAgent : null,
      includeTranscript: _includeTranscript,
      attachResult: _hasConversation && _attachResult,
    );
    if (!mounted) return;
    final job = result.value;
    if (job == null || job.status == 'failed') {
      setState(() {
        _busy = false;
        _error = job?.error.isNotEmpty == true ? job!.error : (result.error ?? context.l10n.sendToAgentFailed);
      });
      return;
    }
    Navigator.of(context).pop(job);
  }

  String _explain(String target) => switch (target) {
        'hermes' => context.l10n.sendToAgentHermesHint,
        'studio' => context.l10n.sendToAgentStudioHint,
        _ => context.l10n.sendToAgentOdysseusHint,
      };

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final preview = _preview;
    final previewCurrent = preview != null && _previewKey == _formKey;
    return SingleChildScrollView(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom + OmiSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(l10n.sendToAgentWho, style: OmiType.subhead.copyWith(color: OmiColors.textPrimary)),
          const SizedBox(height: OmiSpacing.xs),
          for (final target in const ['odysseus', 'hermes', 'studio'])
            InkWell(
              key: Key('send_to_agent_target_$target'),
              borderRadius: OmiRadius.mdAll,
              onTap: _busy ? null : () => setState(() => _target = target),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: OmiSpacing.xs),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Icon(
                    _target == target ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded,
                    size: 22,
                    color: _target == target ? OmiColors.textPrimary : OmiColors.textTertiary,
                  ),
                  const SizedBox(width: OmiSpacing.sm),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(_agentName(context, target), style: OmiType.body.copyWith(color: OmiColors.textPrimary)),
                      Text(_explain(target), style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                    ]),
                  ),
                ]),
              ),
            ),
          if (_target == 'studio')
            Padding(
              padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
              child: DropdownButtonFormField<String>(
                key: const Key('send_to_agent_studio_agent'),
                initialValue: _studioAgent,
                decoration: InputDecoration(labelText: l10n.connectionsStudioAgent, filled: true),
                items: [for (final a in studioAgents) DropdownMenuItem(value: a, child: Text(a))],
                onChanged: _busy ? null : (v) => setState(() => _studioAgent = v ?? _studioAgent),
              ),
            ),
          const SizedBox(height: OmiSpacing.sm),
          TextField(
            key: const Key('send_to_agent_prompt'),
            controller: _prompt,
            minLines: 2,
            maxLines: 6,
            style: OmiType.body.copyWith(color: OmiColors.textPrimary),
            decoration: InputDecoration(
              labelText: l10n.sendToAgentPrompt,
              hintText: l10n.sendToAgentPromptHint,
              filled: true,
              fillColor: OmiColors.surface2,
              border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
            ),
          ),
          if (_hasConversation) ...[
            OmiSettingsRow.toggle(
              key: const Key('send_to_agent_transcript'),
              title: l10n.connectionsIncludeTranscript,
              value: _includeTranscript,
              onChanged: _busy ? null : (v) => setState(() => _includeTranscript = v),
            ),
            OmiSettingsRow.toggle(
              key: const Key('send_to_agent_attach'),
              title: l10n.sendToAgentAttach,
              value: _attachResult,
              onChanged: _busy ? null : (v) => setState(() => _attachResult = v),
            ),
          ],
          const SizedBox(height: OmiSpacing.sm),
          if (preview != null && previewCurrent && preview.suggestedTarget != _target)
            Padding(
              padding: const EdgeInsets.only(bottom: OmiSpacing.xs),
              child: Row(children: [
                Expanded(
                  child: Text(l10n.sendToAgentSuggest(_agentName(context, preview.suggestedTarget)),
                      style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                ),
                TextButton(
                  key: const Key('send_to_agent_use_suggested'),
                  onPressed: _busy ? null : () => setState(() => _target = preview.suggestedTarget),
                  child: Text(l10n.sendToAgentUseSuggested(_agentName(context, preview.suggestedTarget))),
                ),
              ]),
            ),
          if (preview != null && previewCurrent) ...[
            Text(l10n.sendToAgentWhatLeaves(preview.destination),
                style: OmiType.subhead.copyWith(color: OmiColors.textPrimary)),
            const SizedBox(height: OmiSpacing.xs),
            Container(
              key: const Key('send_to_agent_preview'),
              width: double.infinity,
              constraints: const BoxConstraints(maxHeight: 220),
              padding: const EdgeInsets.all(OmiSpacing.sm),
              decoration: BoxDecoration(color: OmiColors.surface2, borderRadius: OmiRadius.mdAll),
              child: SingleChildScrollView(
                child: SelectableText(preview.content,
                    style: OmiType.footnote.copyWith(color: OmiColors.textSecondary, fontFamily: 'monospace')),
              ),
            ),
            const SizedBox(height: OmiSpacing.sm),
          ],
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: OmiSpacing.xs),
              child: Text(_error!,
                  key: const Key('send_to_agent_error'), style: OmiType.footnote.copyWith(color: OmiColors.danger)),
            ),
          // Stacked: each label is a sentence and must not be cut off.
          SizedBox(
            width: double.infinity,
            child: OmiButton.secondary(
              key: const Key('send_to_agent_show_preview'),
              label: previewCurrent ? l10n.sendToAgentPreviewAgain : l10n.sendToAgentShowPreview,
              onPressed: _busy ? null : _showPreview,
            ),
          ),
          const SizedBox(height: OmiSpacing.xs),
          SizedBox(
            width: double.infinity,
            child: OmiButton(
              key: const Key('send_to_agent_send'),
              label: l10n.sendToAgentSend(_agentName(context, _target)),
              onPressed: _busy || !previewCurrent ? null : _send,
            ),
          ),
          if (!previewCurrent)
            Padding(
              padding: const EdgeInsets.only(top: OmiSpacing.xs),
              child: Text(l10n.sendToAgentPreviewFirst, style: OmiType.caption.copyWith(color: OmiColors.textTertiary)),
            ),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(
              key: const Key('send_to_agent_jobs'),
              onPressed: () => routeToPage(context, const AgentJobsPage()),
              child: Text(l10n.agentJobsTitle),
            ),
          ),
        ],
      ),
    );
  }
}
