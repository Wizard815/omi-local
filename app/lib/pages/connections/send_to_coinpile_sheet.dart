import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// "Send to Coinpile" for a conversation or a task: a note (title and text, drafted from it and
/// editable) or a transaction (account, amount, date, description). The fields on screen are
/// exactly what leaves. Coinpile only asks: nothing changes there until the user answers Yes on
/// Coinpile's Agent page.
Future<void> showSendToCoinpileSheet(BuildContext context,
    {String? conversationId, String? taskId, ConnectionsApi api = const ConnectionsApi()}) async {
  final sent = await showOmiSheet<bool>(
    context: context,
    title: context.l10n.sendToCoinpileTitle,
    builder: (_) => SendToCoinpileSheet(conversationId: conversationId, taskId: taskId, api: api),
  );
  if (sent == true && context.mounted) AppSnackbar.showSnackbar(context.l10n.sendToCoinpileSent);
}

class SendToCoinpileSheet extends StatefulWidget {
  const SendToCoinpileSheet({super.key, this.conversationId, this.taskId, required this.api});

  final String? conversationId;
  final String? taskId;
  final ConnectionsApi api;

  @override
  State<SendToCoinpileSheet> createState() => _SendToCoinpileSheetState();
}

class _SendToCoinpileSheetState extends State<SendToCoinpileSheet> {
  final _title = TextEditingController();
  final _body = TextEditingController();
  final _amount = TextEditingController();
  final _date = TextEditingController();
  String _kind = 'note';
  List<CoinpileAccount> _accounts = const [];
  String? _account;
  String? _error;
  bool _busy = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _title.dispose();
    _body.dispose();
    _amount.dispose();
    _date.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final draft = await widget.api.coinpileDraft(conversationId: widget.conversationId, taskId: widget.taskId);
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (draft.value != null) {
        _title.text = draft.value!.title;
        _body.text = draft.value!.body;
      } else {
        _error = draft.error;
      }
    });
  }

  Future<void> _loadAccounts() async {
    if (_accounts.isNotEmpty) return;
    final result = await widget.api.coinpileAccounts();
    if (!mounted) return;
    setState(() {
      _accounts = result.value?.accounts ?? const [];
      final problem = result.value?.error ?? result.error;
      if (problem != null && problem.isNotEmpty) _error = problem;
    });
  }

  Future<void> _send() async {
    final l10n = context.l10n;
    final amount = double.tryParse(_amount.text.trim().replaceAll(',', '.'));
    if (_kind == 'transaction' && (_account == null || amount == null)) {
      setState(() => _error = l10n.sendToCoinpileNeedsAccount);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final result = await widget.api.coinpileSend({
      'kind': _kind,
      if (widget.conversationId != null) 'conversation_id': widget.conversationId,
      if (widget.taskId != null) 'task_id': widget.taskId,
      'title': _title.text.trim(),
      'body': _body.text.trim(),
      if (_kind == 'transaction') ...{
        'account_id': _account,
        'amount': amount,
        'description': _title.text.trim(),
        if (_date.text.trim().isNotEmpty) 'date': _date.text.trim(),
      },
    });
    if (!mounted) return;
    final value = result.value;
    if (value != null && value.ok) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      _busy = false;
      _error = value?.error.isNotEmpty == true ? value!.error : (result.error ?? l10n.sendToCoinpileFailed);
    });
  }

  InputDecoration _decoration(String label, {String? hint}) => InputDecoration(
        labelText: label,
        hintText: hint,
        filled: true,
        fillColor: OmiColors.surface2,
        border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
      );

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return SingleChildScrollView(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom + OmiSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l10n.sendToCoinpileIntro, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
          const SizedBox(height: OmiSpacing.sm),
          SegmentedButton<String>(
            key: const Key('send_to_coinpile_kind'),
            segments: [
              ButtonSegment(value: 'note', label: Text(l10n.sendToCoinpileNote)),
              ButtonSegment(value: 'transaction', label: Text(l10n.sendToCoinpileTransaction)),
            ],
            selected: {_kind},
            onSelectionChanged: _busy
                ? null
                : (s) {
                    setState(() => _kind = s.first);
                    if (_kind == 'transaction') _loadAccounts();
                  },
          ),
          const SizedBox(height: OmiSpacing.sm),
          TextField(
            key: const Key('send_to_coinpile_title'),
            controller: _title,
            decoration: _decoration(_kind == 'note' ? l10n.sendToCoinpileNoteTitle : l10n.sendToCoinpileDescription),
          ),
          const SizedBox(height: OmiSpacing.sm),
          if (_kind == 'note')
            TextField(
              key: const Key('send_to_coinpile_body'),
              controller: _body,
              minLines: 3,
              maxLines: 8,
              decoration: _decoration(l10n.sendToCoinpileNoteBody),
            )
          else ...[
            DropdownButtonFormField<String>(
              key: const Key('send_to_coinpile_account'),
              initialValue: _account,
              decoration: _decoration(l10n.sendToCoinpileAccount),
              items: [
                for (final a in _accounts)
                  DropdownMenuItem(value: a.id, child: Text(a.name.isEmpty ? a.id : '${a.name} (${a.type})')),
              ],
              onChanged: _busy ? null : (v) => setState(() => _account = v),
            ),
            const SizedBox(height: OmiSpacing.sm),
            TextField(
              key: const Key('send_to_coinpile_amount'),
              controller: _amount,
              keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
              decoration: _decoration(l10n.sendToCoinpileAmount, hint: '-120.00'),
            ),
            const SizedBox(height: OmiSpacing.sm),
            TextField(
              key: const Key('send_to_coinpile_date'),
              controller: _date,
              decoration: _decoration(l10n.sendToCoinpileDate, hint: 'YYYY-MM-DD'),
            ),
          ],
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: OmiSpacing.xs),
              child: Text(_error!, style: OmiType.footnote.copyWith(color: OmiColors.danger)),
            ),
          const SizedBox(height: OmiSpacing.sm),
          OmiButton(
            key: const Key('send_to_coinpile_send'),
            label: l10n.sendToCoinpileSend,
            onPressed: _busy ? null : _send,
          ),
        ],
      ),
    );
  }
}
