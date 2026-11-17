// The editing sheet for a day summary: a paragraph to add, or the correction for a selected
// quote. It is an OmiEditSheet, so typed text is never lost to a stray tap outside, a swipe
// down or system back: those ask "Discard changes?" first (docs/ux-contract.md §2).
import 'package:flutter/material.dart';

import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// Resolves the trimmed text on Save, or null when the sheet was left without saving.
Future<String?> showDayReviewTextSheet(
  BuildContext context, {
  required String title,
  String? hint,
  String? quote,
  String initialText = '',
  int minLines = 3,
}) {
  return showOmiEditSheet<String>(
    context: context,
    builder: (_) =>
        DayReviewTextSheet(title: title, hint: hint, quote: quote, initialText: initialText, minLines: minLines),
  );
}

class DayReviewTextSheet extends StatefulWidget {
  const DayReviewTextSheet({
    super.key,
    required this.title,
    this.hint,
    this.quote,
    this.initialText = '',
    this.minLines = 3,
  });

  final String title;
  final String? hint;

  /// Prefilled text (e.g. a suggested name); the sheet counts as edited only once it differs.
  final String initialText;
  final int minLines;

  /// The selected summary text this correction is about, shown above the field.
  final String? quote;

  @override
  State<DayReviewTextSheet> createState() => _DayReviewTextSheetState();
}

class _DayReviewTextSheetState extends State<DayReviewTextSheet> {
  late final TextEditingController _controller = TextEditingController(text: widget.initialText);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  String get _text => _controller.text.trim();

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return OmiEditSheet(
      title: widget.title,
      isDirty: _text.isNotEmpty && _text != widget.initialText.trim(),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (widget.quote != null)
            Padding(
              padding: const EdgeInsets.only(bottom: OmiSpacing.xs),
              child: Text(
                '“${widget.quote}”',
                maxLines: 4,
                overflow: TextOverflow.ellipsis,
                style: OmiType.subhead.copyWith(color: OmiColors.textSecondary, fontStyle: FontStyle.italic),
              ),
            ),
          TextField(
            key: const Key('day_review_text_field'),
            controller: _controller,
            autofocus: true,
            minLines: widget.minLines,
            maxLines: widget.minLines > 1 ? 8 : 1,
            style: OmiType.body,
            decoration: InputDecoration(
              hintText: widget.hint,
              hintStyle: OmiType.body.copyWith(color: OmiColors.textTertiary),
              filled: true,
              fillColor: OmiColors.surface2,
              border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: OmiSpacing.sm),
          Row(
            children: [
              Expanded(
                child: OmiButton.secondary(
                  label: l10n.cancel,
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ),
              const SizedBox(width: OmiSpacing.sm),
              Expanded(
                child: OmiButton(
                  key: const Key('day_review_text_save'),
                  label: l10n.save,
                  onPressed: _text.isEmpty ? null : () => Navigator.of(context).pop(_text),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
