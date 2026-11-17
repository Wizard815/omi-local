import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/pages/connections/agent_jobs_page.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// Studio agents a job can go to (Studio's ORB_WORK_AGENTS).
const studioAgents = ['ekko-agent', 'hermes', 'claude-code', 'codex', 'pi', 'grok', 'opencode'];

/// omi-local: Settings → Connections. Where Omi may send this user's things: the family wiki,
/// Odysseus (simple asks), Hermes (agentic jobs), Studio (coding agents), Coinpile and Pondering Orb.
/// Each is off until turned on here; nothing leaves without the user's OK (a day summary when they
/// approve the day, everything else on an explicit Send after a preview). Tokens are write-only.
class ConnectionsPage extends StatefulWidget {
  const ConnectionsPage({super.key, this.api = const ConnectionsApi()});

  final ConnectionsApi api;

  @override
  State<ConnectionsPage> createState() => _ConnectionsPageState();
}

class _ConnectionsPageState extends State<ConnectionsPage> {
  ConnectionsState? _state;
  String? _error;
  bool _busy = false;
  final Map<String, ({bool ok, String text})> _tests = {};
  final Map<String, Map<String, TextEditingController>> _fields = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final group in _fields.values) {
      for (final c in group.values) {
        c.dispose();
      }
    }
    super.dispose();
  }

  TextEditingController _field(String target, String key, String initial) {
    final group = _fields.putIfAbsent(target, () => {});
    return group.putIfAbsent(key, () => TextEditingController(text: initial));
  }

  void _resetFields(ConnectionsState state) {
    for (final entry in _fields.entries) {
      final target = state[entry.key];
      for (final f in entry.value.entries) {
        f.value.text = switch (f.key) {
          'url' => target.ownUrl,
          'token' || 'webhook_secret' => '',
          _ => target.text(f.key),
        };
      }
    }
  }

  Future<void> _load() async {
    final result = await widget.api.load();
    if (!mounted) return;
    setState(() {
      _state = result.value ?? _state;
      _error = result.ok ? null : (result.error ?? context.l10n.connectionsLoadFailed);
    });
    if (result.value != null) _resetFields(result.value!);
  }

  Future<void> _update(String target, Map<String, Object?> changes) async {
    setState(() => _busy = true);
    final result = await widget.api.update(target, changes);
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (result.value != null) _state = result.value;
    });
    if (result.value != null) {
      _resetFields(result.value!);
    } else {
      AppSnackbar.showSnackbarError(result.error ?? context.l10n.connectionsSaveFailed);
    }
  }

  Future<void> _save(String target) {
    final fields = _fields[target] ?? const {};
    final changes = <String, Object?>{};
    for (final entry in fields.entries) {
      final value = entry.value.text.trim();
      // A secret left empty keeps the stored one (Clear removes it explicitly).
      if ((entry.key == 'token' || entry.key == 'webhook_secret') && value.isEmpty) continue;
      changes[entry.key] = value;
    }
    return _update(target, changes);
  }

  Future<void> _test(String target) async {
    setState(() => _busy = true);
    final result = await widget.api.test(target);
    if (!mounted) return;
    final l10n = context.l10n;
    setState(() {
      _busy = false;
      final value = result.value;
      _tests[target] = value == null
          ? (ok: false, text: result.error ?? l10n.connectionsTestFailed)
          : (ok: value.ok, text: value.ok ? l10n.connectionsTestOk(value.detail) : value.error);
    });
  }

  String _title(String target) {
    final l10n = context.l10n;
    return switch (target) {
      'wiki' => l10n.connectionsWikiTitle,
      'odysseus' => l10n.connectionsOdysseusTitle,
      'hermes' => l10n.connectionsHermesTitle,
      'studio' => l10n.connectionsStudioTitle,
      'coinpile' => l10n.connectionsCoinpileTitle,
      _ => l10n.connectionsOrbTitle,
    };
  }

  String _purpose(String target) {
    final l10n = context.l10n;
    return switch (target) {
      'wiki' => l10n.connectionsWikiPurpose,
      'odysseus' => l10n.connectionsOdysseusPurpose,
      'hermes' => l10n.connectionsHermesPurpose,
      'studio' => l10n.connectionsStudioPurpose,
      'coinpile' => l10n.connectionsCoinpilePurpose,
      _ => l10n.connectionsOrbPurpose,
    };
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final state = _state;
    return Scaffold(
      backgroundColor: OmiColors.surface0,
      appBar: AppBar(
        backgroundColor: OmiColors.surface0,
        title: Text(l10n.connectionsTitle),
        actions: [
          IconButton(
            key: const Key('connections_agent_jobs'),
            tooltip: l10n.agentJobsTitle,
            icon: const Icon(Icons.smart_toy_outlined),
            onPressed: () => routeToPage(context, const AgentJobsPage()),
          ),
        ],
      ),
      body: state == null
          ? Center(
              child: _error == null
                  ? const OmiSpinner()
                  : Column(mainAxisSize: MainAxisSize.min, children: [
                      Text(_error!, style: OmiType.body.copyWith(color: OmiColors.textSecondary)),
                      TextButton(onPressed: _load, child: Text(l10n.retry)),
                    ]),
            )
          : ListView(
              key: const Key('connections_list'),
              padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xs, OmiSpacing.md, OmiSpacing.xl),
              children: [
                Text(l10n.connectionsIntro, style: OmiType.subhead.copyWith(color: OmiColors.textSecondary)),
                if (state.conversationWebhookToWiki) ...[
                  const SizedBox(height: OmiSpacing.sm),
                  _notice(l10n.connectionsWebhookNotice, key: const Key('connections_webhook_notice')),
                ],
                const SizedBox(height: OmiSpacing.sm),
                for (final target in connectionTargets) _card(state[target]),
              ],
            ),
    );
  }

  Widget _notice(String text, {Key? key}) => Container(
        key: key,
        padding: const EdgeInsets.all(OmiSpacing.sm),
        decoration: BoxDecoration(
          color: OmiColors.warning.withValues(alpha: 0.12),
          borderRadius: OmiRadius.mdAll,
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(Icons.info_outline_rounded, size: 18, color: OmiColors.warning),
          const SizedBox(width: OmiSpacing.xs),
          Expanded(child: Text(text, style: OmiType.footnote.copyWith(color: OmiColors.textPrimary))),
        ]),
      );

  Widget _statusChip(ConnectionTarget t) {
    final l10n = context.l10n;
    final (label, color) = t.ready
        ? (l10n.connectionsStatusOn, OmiColors.success)
        : t.enabled
            ? (l10n.connectionsStatusNeedsSetup, OmiColors.warning)
            : (l10n.connectionsStatusOff, OmiColors.textTertiary);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.xs, vertical: 2),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: OmiRadius.mdAll),
      child: Text(label, style: OmiType.caption.copyWith(color: color)),
    );
  }

  Widget _card(ConnectionTarget t) {
    final l10n = context.l10n;
    final test = _tests[t.name];
    return Padding(
      key: Key('connections_card_${t.name}'),
      padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
      // A Material (not a decorated box) so the tile's ink and background paint on the card.
      child: Material(
        color: OmiColors.surface1,
        borderRadius: OmiRadius.lgAll,
        clipBehavior: Clip.antiAlias,
        child: Theme(
          data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            key: Key('connections_tile_${t.name}'),
            tilePadding: const EdgeInsets.symmetric(horizontal: OmiSpacing.md),
            childrenPadding: const EdgeInsets.fromLTRB(OmiSpacing.md, 0, OmiSpacing.md, OmiSpacing.md),
            title: Row(children: [
              Expanded(child: Text(_title(t.name), style: OmiType.headline.copyWith(color: OmiColors.textPrimary))),
              _statusChip(t),
            ]),
            subtitle: Text(_purpose(t.name), style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
            children: [
              OmiSettingsRow.toggle(
                key: Key('connections_enabled_${t.name}'),
                title: l10n.connectionsUseThis,
                value: t.enabled,
                onChanged: _busy ? null : (v) => _update(t.name, {'enabled': v}),
              ),
              const SizedBox(height: OmiSpacing.xs),
              _text(t.name, 'url', l10n.connectionsAddress, t.ownUrl,
                  hint: t.urlFromEnv ? l10n.connectionsAddressFromServer(t.url) : 'http://192.168.1.20:8080'),
              _text(t.name, 'token', _tokenLabel(t.name), '',
                  obscure: true,
                  helper: t.hasToken
                      ? (t.tokenFromEnv ? l10n.connectionsTokenFromServer : l10n.connectionsTokenSaved)
                      : l10n.connectionsTokenNone),
              ..._options(t),
              Row(children: [
                Expanded(
                  child: OmiButton(
                    key: Key('connections_save_${t.name}'),
                    label: l10n.connectionsSave,
                    onPressed: _busy ? null : () => _save(t.name),
                  ),
                ),
                const SizedBox(width: OmiSpacing.sm),
                Expanded(
                  child: OmiButton.secondary(
                    key: Key('connections_test_${t.name}'),
                    label: l10n.connectionsTest,
                    onPressed: _busy ? null : () => _test(t.name),
                  ),
                ),
              ]),
              if (t.hasToken && !t.tokenFromEnv)
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton(
                    key: Key('connections_clear_token_${t.name}'),
                    onPressed: _busy ? null : () => _update(t.name, {'token': ''}),
                    child: Text(l10n.connectionsClearToken),
                  ),
                ),
              if (test != null || t.lastCheckOk != null)
                Padding(
                  padding: const EdgeInsets.only(top: OmiSpacing.xs),
                  child: Text(
                    test?.text ?? (t.lastCheckOk == true ? l10n.connectionsLastTestOk : t.lastCheckError),
                    key: Key('connections_test_result_${t.name}'),
                    style: OmiType.footnote.copyWith(
                      color: (test?.ok ?? t.lastCheckOk ?? false) ? OmiColors.success : OmiColors.danger,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  String _tokenLabel(String target) {
    final l10n = context.l10n;
    return switch (target) {
      'wiki' => l10n.connectionsTokenWiki,
      'odysseus' => l10n.connectionsTokenOdysseus,
      'hermes' => l10n.connectionsTokenHermes,
      'studio' => l10n.connectionsTokenStudio,
      'coinpile' => l10n.connectionsTokenCoinpile,
      _ => l10n.connectionsTokenOrb,
    };
  }

  List<Widget> _options(ConnectionTarget t) {
    final l10n = context.l10n;
    switch (t.name) {
      case 'wiki':
        final mode = t.text('send_kept_conversations');
        return [
          _toggle(t, 'send_day_reviews', l10n.connectionsWikiSendDays, l10n.connectionsWikiSendDaysHint),
          _toggle(t, 'require_manual_approval', l10n.connectionsOnlyManual, l10n.connectionsOnlyManualHint,
              fallback: true),
          Padding(
            padding: const EdgeInsets.symmetric(vertical: OmiSpacing.xs),
            child: Text(l10n.connectionsWikiKeptTitle, style: OmiType.subhead.copyWith(color: OmiColors.textPrimary)),
          ),
          SegmentedButton<String>(
            key: const Key('connections_wiki_kept_mode'),
            segments: [
              ButtonSegment(value: 'off', label: Text(l10n.connectionsWikiKeptOff)),
              ButtonSegment(value: 'ask', label: Text(l10n.connectionsWikiKeptAsk)),
              ButtonSegment(value: 'on_keep', label: Text(l10n.connectionsWikiKeptOnKeep)),
            ],
            selected: {mode.isEmpty ? 'off' : mode},
            onSelectionChanged: _busy ? null : (s) => _update('wiki', {'send_kept_conversations': s.first}),
          ),
          const SizedBox(height: OmiSpacing.xs),
          _toggle(t, 'include_transcript', l10n.connectionsIncludeTranscript, l10n.connectionsIncludeTranscriptHint),
          _text('wiki', 'person', l10n.connectionsWikiPerson, t.text('person'), helper: l10n.connectionsWikiPersonHint),
        ];
      case 'odysseus':
        return [
          _text('odysseus', 'endpoint_id', l10n.connectionsOdysseusEndpoint, t.text('endpoint_id'),
              helper: l10n.connectionsOdysseusEndpointHint),
          _text('odysseus', 'model', l10n.connectionsOdysseusModel, t.text('model')),
          _text('odysseus', 'session_id', l10n.connectionsOdysseusChat, t.text('session_id'),
              helper: l10n.connectionsOdysseusChatHint),
        ];
      case 'studio':
        final agent = t.text('default_agent');
        return [
          Padding(
            padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
            child: DropdownButtonFormField<String>(
              key: const Key('connections_studio_agent'),
              initialValue: studioAgents.contains(agent) ? agent : 'ekko-agent',
              decoration: InputDecoration(labelText: l10n.connectionsStudioAgent, filled: true),
              items: [for (final a in studioAgents) DropdownMenuItem(value: a, child: Text(a))],
              onChanged: _busy ? null : (v) => v == null ? null : _update('studio', {'default_agent': v}),
            ),
          ),
          _text('studio', 'workspace', l10n.connectionsStudioWorkspace, t.text('workspace')),
          _text('studio', 'webhook_secret', l10n.connectionsStudioSecret, '',
              obscure: true,
              helper: l10n.connectionsStudioSecretHint(t.text('webhook_path')) +
                  (t.flag('has_webhook_secret') ? ' ${l10n.connectionsTokenSaved}' : '')),
        ];
      case 'orb':
        return [
          _toggle(t, 'log_day_approvals', l10n.connectionsOrbLog, l10n.connectionsOrbLogHint),
          _toggle(t, 'require_manual_approval', l10n.connectionsOnlyManual, l10n.connectionsOnlyManualHint,
              fallback: true),
          _text('orb', 'project', l10n.connectionsOrbProject, t.text('project')),
        ];
      default:
        return const [];
    }
  }

  Widget _toggle(ConnectionTarget t, String key, String title, String subtitle, {bool fallback = false}) =>
      OmiSettingsRow.toggle(
        key: Key('connections_${t.name}_$key'),
        title: title,
        subtitle: subtitle,
        value: t.flag(key, fallback: fallback),
        onChanged: _busy ? null : (v) => _update(t.name, {key: v}),
      );

  Widget _text(String target, String key, String label, String initial,
      {String? hint, String? helper, bool obscure = false}) {
    final controller = _field(target, key, initial);
    return Padding(
      padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
      child: TextField(
        key: Key('connections_${target}_field_$key'),
        controller: controller,
        obscureText: obscure,
        autocorrect: false,
        style: OmiType.body.copyWith(color: OmiColors.textPrimary),
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          helperText: helper,
          helperMaxLines: 3,
          labelStyle: TextStyle(color: OmiColors.textSecondary),
          hintStyle: TextStyle(color: OmiColors.textTertiary),
          helperStyle: TextStyle(color: OmiColors.textTertiary),
          filled: true,
          fillColor: OmiColors.surface2,
          border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
        ),
      ),
    );
  }
}
