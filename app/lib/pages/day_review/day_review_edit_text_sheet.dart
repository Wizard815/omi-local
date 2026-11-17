// "Edit text" for a day summary: the title and each section's heading and text in one OmiEditSheet
// (a summary written before sections existed is one text field). Save returns only what changed;
// leaving with unsaved changes asks "Discard changes?" first (docs/ux-contract.md §2). Backend:
// PUT /v1/users/day-reviews/{date}/text.
import 'package:flutter/material.dart';

import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// What the user changed: a new title, changed sections ({id: (heading, text)}), or the whole text
/// of a summary without sections. Null fields are unchanged.
class DayReviewTextEdit {
  const DayReviewTextEdit({this.title, this.sections, this.summaryText});

  final String? title;
  final Map<String, ({String title, String text})>? sections;
  final String? summaryText;

  /// The edit that puts [review]'s text back for exactly what this edit changed (Undo).
  DayReviewTextEdit revertOn(DayReview review) => DayReviewTextEdit(
        title: title == null ? null : review.title,
        sections: sections == null
            ? null
            : {
                for (final s in review.sections)
                  if (sections!.containsKey(s.id)) s.id: (title: s.title, text: s.text)
              },
        summaryText: summaryText == null ? null : review.summaryText,
      );
}

/// Resolves the changes on Save, or null when the sheet was left without saving.
Future<DayReviewTextEdit?> showDayReviewEditTextSheet(BuildContext context, DayReview review) {
  return showOmiEditSheet<DayReviewTextEdit>(
    context: context,
    builder: (_) => DayReviewEditTextSheet(review: review),
  );
}

class DayReviewEditTextSheet extends StatefulWidget {
  const DayReviewEditTextSheet({super.key, required this.review});

  final DayReview review;

  @override
  State<DayReviewEditTextSheet> createState() => _DayReviewEditTextSheetState();
}

class _DayReviewEditTextSheetState extends State<DayReviewEditTextSheet> {
  late final TextEditingController _title = TextEditingController(text: widget.review.title);
  late final List<DayReviewSection> _sections = widget.review.sections.where((s) => s.text.isNotEmpty).toList();
  late final Map<String, TextEditingController> _headings = {
    for (final s in _sections) s.id: TextEditingController(text: s.title)
  };
  late final Map<String, TextEditingController> _texts = {
    for (final s in _sections) s.id: TextEditingController(text: s.text)
  };
  late final TextEditingController _whole = TextEditingController(text: widget.review.summaryText);

  bool get _sectioned => _sections.isNotEmpty;

  @override
  void dispose() {
    _title.dispose();
    _whole.dispose();
    for (final c in [..._headings.values, ..._texts.values]) {
      c.dispose();
    }
    super.dispose();
  }

  DayReviewTextEdit get _edit {
    final title = _title.text.trim();
    final changed = <String, ({String title, String text})>{};
    for (final s in _sections) {
      final heading = _headings[s.id]!.text.trim();
      final text = _texts[s.id]!.text.trim();
      if (heading != s.title.trim() || text != s.text.trim()) changed[s.id] = (title: heading, text: text);
    }
    final whole = _whole.text.trim();
    return DayReviewTextEdit(
      title: title != widget.review.title.trim() ? title : null,
      sections: changed.isEmpty ? null : changed,
      summaryText: !_sectioned && whole != widget.review.summaryText.trim() ? whole : null,
    );
  }

  bool get _dirty {
    final edit = _edit;
    return edit.title != null || edit.sections != null || edit.summaryText != null;
  }

  bool get _valid =>
      _title.text.trim().isNotEmpty &&
      (_sectioned ? _texts.values.every((c) => c.text.trim().isNotEmpty) : _whole.text.trim().isNotEmpty);

  Widget _field(
    TextEditingController controller, {
    required Key key,
    required String label,
    int minLines = 1,
    TextStyle? style,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: OmiSpacing.xs),
      child: TextField(
        key: key,
        controller: controller,
        minLines: minLines,
        maxLines: minLines > 1 ? 12 : 1,
        style: style ?? OmiType.body,
        decoration: InputDecoration(
          labelText: label,
          filled: true,
          fillColor: OmiColors.surface2,
          border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
        ),
        onChanged: (_) => setState(() {}),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final dirty = _dirty;
    return OmiEditSheet(
      title: l10n.dayReviewEditText,
      isDirty: dirty,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.75),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Flexible(
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _field(_title,
                        key: const Key('day_review_edit_title'),
                        label: l10n.dayReviewEditTitleField,
                        style: OmiType.headline),
                    if (!_sectioned)
                      _field(_whole,
                          key: const Key('day_review_edit_whole'), label: l10n.dayReviewEditSummaryField, minLines: 6),
                    for (final s in _sections) ...[
                      const SizedBox(height: OmiSpacing.xs),
                      _field(_headings[s.id]!,
                          key: Key('day_review_edit_heading_${s.id}'), label: l10n.dayReviewEditHeadingField),
                      _field(_texts[s.id]!,
                          key: Key('day_review_edit_text_${s.id}'), label: l10n.dayReviewEditSummaryField, minLines: 3),
                    ],
                  ],
                ),
              ),
            ),
            const SizedBox(height: OmiSpacing.sm),
            Row(
              children: [
                Expanded(
                  child: OmiButton.secondary(
                    key: const Key('day_review_edit_cancel'),
                    label: l10n.cancel,
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ),
                const SizedBox(width: OmiSpacing.sm),
                Expanded(
                  child: OmiButton(
                    key: const Key('day_review_edit_save'),
                    label: l10n.save,
                    onPressed: dirty && _valid ? () => Navigator.of(context).pop(_edit) : null,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
