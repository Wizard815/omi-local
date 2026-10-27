import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/conversations.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// Edit a conversation's tags: type one, or tap one of the user's existing tags.
/// Saves on close; returns the stored tags (server-normalized) or null when nothing changed.
Future<List<String>?> showConversationTagsSheet(BuildContext context, ServerConversation conversation) {
  return showOmiSheet<List<String>>(
    context: context,
    title: context.l10n.conversationTags,
    builder: (ctx) => _TagsSheet(conversation: conversation),
  );
}

class _TagsSheet extends StatefulWidget {
  final ServerConversation conversation;

  const _TagsSheet({required this.conversation});

  @override
  State<_TagsSheet> createState() => _TagsSheetState();
}

class _TagsSheetState extends State<_TagsSheet> {
  late final List<String> _tags = List<String>.from(widget.conversation.tags);
  List<String> _catalog = const [];
  final _input = TextEditingController();
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    getConversationTagCatalog().then((rows) {
      if (mounted) setState(() => _catalog = [for (final r in rows) r.tag]);
    });
  }

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  String _normalize(String raw) => raw
      .trim()
      .toLowerCase()
      .replaceAll(RegExp(r'[\s/\\,;#]+'), '-')
      .replaceAll(RegExp(r'-{2,}'), '-')
      .replaceAll(RegExp(r'^-+|-+$'), '');

  void _add(String raw) {
    final tag = _normalize(raw);
    if (tag.isEmpty) return;
    setState(() {
      if (!_tags.contains(tag)) _tags.add(tag);
      _input.clear();
    });
  }

  Future<void> _done() async {
    if (_input.text.trim().isNotEmpty) _add(_input.text);
    final changed = _tags.join('\u0000') != widget.conversation.tags.join('\u0000');
    if (!changed) {
      Navigator.pop(context);
      return;
    }
    setState(() => _saving = true);
    final stored = await setConversationTags(widget.conversation.id, _tags);
    if (!mounted) return;
    if (stored == null) {
      setState(() => _saving = false);
      AppSnackbar.showSnackbarError(context.l10n.conversationTagsSaveFailed);
      return;
    }
    Navigator.pop(context, stored);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final suggestions = _catalog.where((t) => !_tags.contains(t)).toList();
    return Padding(
      padding: EdgeInsets.only(bottom: OmiSpacing.md + MediaQuery.of(context).viewInsets.bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(
              key: const Key('conversation_tags_done'),
              onPressed: _saving ? null : _done,
              child: _saving
                  ? const OmiSpinner(size: OmiSpinnerSize.small)
                  : Text(l10n.done, style: OmiType.subhead.copyWith(fontWeight: FontWeight.w600)),
            ),
          ),
          const SizedBox(height: OmiSpacing.xs),
          if (_tags.isEmpty)
            Text(l10n.conversationTagsEmpty, style: OmiType.subhead.copyWith(color: OmiColors.textTertiary))
          else
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final tag in _tags)
                  InputChip(
                    key: Key('conversation_tag_$tag'),
                    label: Text('#$tag'),
                    labelStyle: OmiType.subhead,
                    backgroundColor: OmiColors.chipSurface,
                    deleteIconColor: OmiColors.textSecondary,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(OmiRadius.md)),
                    onDeleted: () => setState(() => _tags.remove(tag)),
                  ),
              ],
            ),
          const SizedBox(height: OmiSpacing.sm),
          TextField(
            key: const Key('conversation_tags_input'),
            controller: _input,
            autofocus: true,
            textInputAction: TextInputAction.done,
            onSubmitted: _add,
            style: OmiType.body,
            decoration: InputDecoration(
              hintText: l10n.conversationTagsAdd,
              hintStyle: OmiType.body.copyWith(color: OmiColors.textTertiary),
              filled: true,
              fillColor: OmiColors.surface2,
              suffixIcon: IconButton(
                icon: Icon(Icons.add_rounded, color: OmiColors.textSecondary),
                onPressed: () => _add(_input.text),
              ),
              border:
                  OutlineInputBorder(borderRadius: BorderRadius.circular(OmiRadius.md), borderSide: BorderSide.none),
            ),
          ),
          if (suggestions.isNotEmpty) ...[
            const SizedBox(height: OmiSpacing.sm),
            Text(l10n.conversationTagsSuggestions, style: OmiType.footnote.copyWith(color: OmiColors.textTertiary)),
            const SizedBox(height: OmiSpacing.xxs),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final tag in suggestions.take(30))
                  ActionChip(
                    label: Text('#$tag'),
                    labelStyle: OmiType.subhead.copyWith(color: OmiColors.textSecondary),
                    backgroundColor: OmiColors.surface2,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(OmiRadius.md)),
                    onPressed: () => _add(tag),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
