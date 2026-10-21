import 'package:flutter/material.dart';

import 'package:provider/provider.dart';

import 'package:omi/backend/http/api/chat_model_shortcuts.dart';
import 'package:omi/providers/message_provider.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// "/model": choose which model each chat shortcut (e.g. /adult) answers with.
class ChatModelShortcutsSheet extends StatefulWidget {
  const ChatModelShortcutsSheet({super.key});

  static Future<void> show(BuildContext context) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF1F1F25),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => const ChatModelShortcutsSheet(),
    );
  }

  @override
  State<ChatModelShortcutsSheet> createState() => _ChatModelShortcutsSheetState();
}

class _ChatModelShortcutsSheetState extends State<ChatModelShortcutsSheet> {
  final _newName = TextEditingController();
  String? _newModel;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await context.read<MessageProvider>().refreshChatModelShortcuts();
      if (mounted) setState(() => _loading = false);
    });
  }

  @override
  void dispose() {
    _newName.dispose();
    super.dispose();
  }

  Future<void> _save(String name, String? model) async {
    final ok = await context.read<MessageProvider>().saveChatModelShortcut(name, model);
    if (!ok && mounted) AppSnackbar.showSnackbarError(context.l10n.chatModelShortcutsSaveFailed);
  }

  List<DropdownMenuItem<String>> _items(ChatModelShortcutsState state, String? current) {
    final ids = state.availableModels.map((m) => m.id).toList();
    if (current != null && current.isNotEmpty && !ids.contains(current)) ids.insert(0, current);
    return [
      DropdownMenuItem<String>(value: '', child: Text(context.l10n.chatModelShortcutsNotSet)),
      ...ids.map((id) => DropdownMenuItem<String>(value: id, child: Text(id, overflow: TextOverflow.ellipsis))),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<MessageProvider>().chatModelShortcuts;
    final l10n = context.l10n;
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(20, 16, 20, 16 + MediaQuery.of(context).viewInsets.bottom),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(l10n.chatModelShortcutsTitle,
                  style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text(l10n.chatModelShortcutsHint, style: TextStyle(color: Colors.grey.shade400, fontSize: 13)),
              const SizedBox(height: 16),
              if (_loading && !state.enabled)
                const Center(child: Padding(padding: EdgeInsets.all(16), child: CircularProgressIndicator()))
              else if (!state.enabled)
                Text(l10n.chatShortcutsUnavailable, style: TextStyle(color: Colors.grey.shade300))
              else ...[
                Text(l10n.chatModelShortcutsBaseModel(state.baseModel),
                    style: TextStyle(color: Colors.grey.shade500, fontSize: 12.5)),
                const SizedBox(height: 12),
                for (final shortcut in state.shortcuts)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 92,
                          child: Text('/${shortcut.name}',
                              style: const TextStyle(color: Colors.white, fontFamily: 'Ubuntu Mono', fontSize: 15)),
                        ),
                        Expanded(
                          child: DropdownButton<String>(
                            isExpanded: true,
                            dropdownColor: const Color(0xFF2A2A30),
                            value: shortcut.model ?? '',
                            items: _items(state, shortcut.model),
                            onChanged: (value) => _save(shortcut.name, value),
                          ),
                        ),
                      ],
                    ),
                  ),
                const Divider(color: Color(0xFF35343B)),
                Row(
                  children: [
                    SizedBox(
                      width: 92,
                      child: TextField(
                        controller: _newName,
                        style: const TextStyle(color: Colors.white, fontFamily: 'Ubuntu Mono'),
                        decoration: InputDecoration(hintText: l10n.chatModelShortcutsAddLabel, isDense: true),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: DropdownButton<String>(
                        isExpanded: true,
                        dropdownColor: const Color(0xFF2A2A30),
                        value: _newModel ?? '',
                        items: _items(state, null),
                        onChanged: (value) => setState(() => _newModel = value),
                      ),
                    ),
                    TextButton(
                      onPressed: () async {
                        final name = _newName.text.trim().replaceFirst(RegExp('^/'), '').toLowerCase();
                        if (name.isEmpty || (_newModel ?? '').isEmpty) return;
                        await _save(name, _newModel);
                        if (mounted) {
                          _newName.clear();
                          setState(() => _newModel = null);
                        }
                      },
                      child: Text(l10n.chatModelShortcutsAddButton),
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
