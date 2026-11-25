import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/pages/connections/connections_page.dart' show connectionProblem;
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// "Send to wiki" for one conversation: exactly what the wiki gets (the edited title, overview,
/// action items, and the transcript if the user chose that in Connections) and where, then Send.
/// A conversation sent before says when, and offers "Send revised copy" (filed as "(revised n)").
Future<void> showSendToWikiSheet(BuildContext context, String conversationId,
    {ConnectionsApi api = const ConnectionsApi()}) async {
  final sent = await showOmiSheet<bool>(
    context: context,
    title: context.l10n.sendToWikiTitle,
    builder: (_) => SendToWikiSheet(conversationId: conversationId, api: api),
  );
  if (sent == true && context.mounted) AppSnackbar.showSnackbar(context.l10n.sendToWikiSent);
}

class SendToWikiSheet extends StatefulWidget {
  const SendToWikiSheet({super.key, required this.conversationId, required this.api});

  final String conversationId;
  final ConnectionsApi api;

  @override
  State<SendToWikiSheet> createState() => _SendToWikiSheetState();
}

class _SendToWikiSheetState extends State<SendToWikiSheet> {
  OutboundPreview? _preview;
  String? _error;
  bool _busy = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final result = await widget.api.previewConversation(widget.conversationId);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _preview = result.value;
      _error = result.ok ? null : connectionProblem(context, result, context.l10n.sendToWikiFailed);
    });
  }

  Future<void> _send() async {
    final revised = _preview?.alreadySent ?? false;
    setState(() => _busy = true);
    final result = await widget.api.sendConversation(widget.conversationId, revised: revised);
    if (!mounted) return;
    final value = result.value;
    if (value != null && value.ok) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      _busy = false;
      _error = value?.error.isNotEmpty == true
          ? value!.error
          : connectionProblem(context, result, context.l10n.sendToWikiFailed);
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final preview = _preview;
    final lastSent = preview?.receipts.where((r) => r.sent).lastOrNull;
    return SingleChildScrollView(
      padding: const EdgeInsets.only(bottom: OmiSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (preview == null && _busy) const Center(child: OmiSpinner()),
          if (preview != null) ...[
            Text(l10n.sendToWikiWhere(preview.destination),
                key: const Key('send_to_wiki_destination'),
                style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
            const SizedBox(height: OmiSpacing.xs),
            Text(preview.title, style: OmiType.headline.copyWith(color: OmiColors.textPrimary)),
            const SizedBox(height: OmiSpacing.xs),
            Container(
              key: const Key('send_to_wiki_preview'),
              width: double.infinity,
              constraints: const BoxConstraints(maxHeight: 300),
              padding: const EdgeInsets.all(OmiSpacing.sm),
              decoration: BoxDecoration(color: OmiColors.surface2, borderRadius: OmiRadius.mdAll),
              child: SingleChildScrollView(
                child: SelectableText(preview.content,
                    style: OmiType.footnote.copyWith(color: OmiColors.textSecondary, fontFamily: 'monospace')),
              ),
            ),
            if (lastSent != null)
              Padding(
                padding: const EdgeInsets.only(top: OmiSpacing.xs),
                child: Text(
                  l10n.sendToWikiAlreadySent(OmiDateFormat.of(context).dateTime(lastSent.sentAt!.toLocal())),
                  key: const Key('send_to_wiki_already_sent'),
                  style: OmiType.footnote.copyWith(color: OmiColors.warning),
                ),
              ),
          ],
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: OmiSpacing.xs),
              child: Text(_error!, style: OmiType.footnote.copyWith(color: OmiColors.danger)),
            ),
          const SizedBox(height: OmiSpacing.sm),
          if (preview != null)
            OmiButton(
              key: const Key('send_to_wiki_send'),
              expand: true,
              label: preview.alreadySent ? l10n.sendToWikiSendRevised : l10n.sendToWikiSend,
              onPressed: _busy ? null : _send,
            ),
        ],
      ),
    );
  }
}
