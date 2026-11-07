import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/ui/omi_tokens.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/day_review/summary_segments.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// End-of-day review for one local date.
///
/// Before review: star the conversations to keep, tick clips to merge them
/// into one, drop the noise; everything else is rolled into one long day
/// summary (and leaves the conversation list). On a self-hosted server the
/// clips arrive unprocessed (transcript only) and only what you keep or merge
/// gets the full treatment.
/// After: the summary reads like a doc you can correct — select text to attach
/// a correction or redact it, add whole paragraphs, then "Apply corrections".
class DayReviewPage extends StatefulWidget {
  final String date;

  const DayReviewPage({super.key, required this.date});

  static String todayKey([DateTime? now]) => DateFormat('yyyy-MM-dd').format(now ?? DateTime.now());

  @override
  State<DayReviewPage> createState() => _DayReviewPageState();
}

class _DayReviewPageState extends State<DayReviewPage> {
  DayReviewDay? _day;
  String? _error;
  String? _busyMessage;
  final Set<String> _keep = {};
  final Set<String> _drop = {};
  final List<List<String>> _groups = [];

  /// Clips ticked for the next merge group.
  final Set<String> _selected = {};

  /// Sections whose conversation list is unfolded in the review.
  final Set<String> _openSections = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final result = await getDayReviewDay(widget.date);
    if (!mounted) return;
    setState(() {
      if (result.ok) {
        _day = result.value;
        _error = null;
        _keep
          ..clear()
          ..addAll(_day!.conversations.where((c) => c.starred && !c.rolledUp && !c.dropped).map((c) => c.id));
        _drop.clear();
        _groups.clear();
        _selected.clear();
      } else {
        _error = result.error ?? context.l10n.dayReviewLoadFailed;
      }
    });
  }

  Future<void> _busy(String message, Future<void> Function() work) async {
    setState(() => _busyMessage = message);
    try {
      await work();
    } finally {
      if (mounted) setState(() => _busyMessage = null);
    }
  }

  void _applyReview(DayReviewResult<DayReview> result) {
    if (!mounted) return;
    if (!result.ok) {
      AppSnackbar.showSnackbarError(result.error ?? context.l10n.dayReviewActionFailed);
      return;
    }
    setState(() {
      _day = DayReviewDay(
        date: _day!.date,
        review: result.value,
        conversations: _day!.conversations,
        processingCount: _day!.processingCount,
      );
    });
  }

  String get _prettyDate {
    final parsed = DateTime.tryParse(widget.date);
    return parsed == null ? widget.date : DateFormat.yMMMMEEEEd().format(parsed);
  }

  // ---------------------------------------------------------------- actions

  Set<String> get _grouped => {for (final g in _groups) ...g};

  int get _rolledCount => _day!.conversations
      .where((c) => !_keep.contains(c.id) && !_drop.contains(c.id) && !_grouped.contains(c.id))
      .length;

  void _toggleKeep(String id) => setState(() {
        if (_keep.contains(id)) {
          _keep.remove(id);
        } else {
          _keep.add(id);
          _drop.remove(id);
          _selected.remove(id);
        }
      });

  void _toggleDrop(String id) => setState(() {
        if (_drop.contains(id)) {
          _drop.remove(id);
        } else {
          _drop.add(id);
          _keep.remove(id);
          _selected.remove(id);
        }
      });

  void _toggleSelected(String id) => setState(() {
        if (_selected.contains(id)) {
          _selected.remove(id);
        } else {
          _selected.add(id);
          _keep.remove(id);
          _drop.remove(id);
        }
      });

  void _mergeSelected() {
    if (_selected.length < 2) return;
    final order = {for (var i = 0; i < _day!.conversations.length; i++) _day!.conversations[i].id: i};
    setState(() {
      _groups.add(_selected.toList()..sort((a, b) => (order[a] ?? 0).compareTo(order[b] ?? 0)));
      _selected.clear();
    });
  }

  void _ungroup(List<String> group) => setState(() => _groups.remove(group));

  /// One tap: starred conversations stay, everything else becomes the sectioned day summary.
  Future<void> _bundle() async {
    final l10n = context.l10n;
    await _busy(l10n.dayReviewBundleWriting, () async {
      final result = await bundleDayReview(widget.date);
      if (result.ok) {
        await _load();
      } else {
        _applyReview(result);
      }
    });
  }

  Future<void> _favoriteSection(DayReviewSection section, bool favorited) async {
    final result = await favoriteDayReviewSection(widget.date, section.id, favorited);
    if (!mounted) return;
    if (result.ok) {
      await _load();
    } else {
      _applyReview(result);
    }
  }

  Future<void> _finalize() async {
    final l10n = context.l10n;
    final rolled = _rolledCount;
    if (rolled > 0 || _groups.isNotEmpty || _drop.isNotEmpty) {
      final extra = _groups.isNotEmpty || _drop.isNotEmpty
          ? '\n\n${l10n.dayReviewConfirmExtra(_groups.length, _drop.length)}'
          : '';
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.dayReviewConfirmTitle),
          content: Text('${l10n.dayReviewConfirmBody(rolled)}$extra'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.dayReviewConfirmAction)),
          ],
        ),
      );
      if (confirmed != true) return;
    }
    await _busy(l10n.dayReviewWriting, () async {
      final result = await finalizeDayReview(
        widget.date,
        _keep.toList(),
        mergeGroups: _groups.map((g) => List<String>.from(g)).toList(),
        dropConversationIds: _drop.toList(),
      );
      if (result.ok) {
        await _load();
      } else if (mounted) {
        AppSnackbar.showSnackbarError(result.error ?? l10n.dayReviewActionFailed);
      }
    });
  }

  Future<void> _regenerate() async {
    await _busy(context.l10n.dayReviewApplying, () async {
      _applyReview(await regenerateDayReview(widget.date));
    });
  }

  Future<void> _reopen() async {
    final l10n = context.l10n;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.dayReviewReopenTitle),
        content: Text(l10n.dayReviewReopenBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.dayReviewReopenAction)),
        ],
      ),
    );
    if (confirmed != true) return;
    await _busy(l10n.dayReviewReopening, () async {
      final result = await reopenDayReview(widget.date);
      if (!result.ok) {
        if (mounted) AppSnackbar.showSnackbarError(result.error ?? l10n.dayReviewActionFailed);
        return;
      }
      await _load();
    });
  }

  Future<String?> _askText({required String title, String? hint, String? quote}) {
    final controller = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (quote != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text('“$quote”', style: TextStyle(color: OmiColors.textSecondary, fontStyle: FontStyle.italic)),
              ),
            TextField(
              controller: controller,
              autofocus: true,
              minLines: 2,
              maxLines: 8,
              decoration: InputDecoration(hintText: hint),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(context.l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: Text(context.l10n.save),
          ),
        ],
      ),
    );
  }

  Future<void> _addParagraph(int? afterParagraph) async {
    final text =
        await _askText(title: context.l10n.dayReviewAddParagraphTitle, hint: context.l10n.dayReviewAddParagraphHint);
    if (text == null || text.isEmpty) return;
    _applyReview(await addDayReviewCorrection(
      widget.date,
      type: DayReviewCorrectionType.paragraph,
      text: text,
      afterParagraph: afterParagraph,
    ));
  }

  Future<void> _correctSelection(int start, int end, String quote) async {
    final text = await _askText(
      title: context.l10n.dayReviewCorrectTitle,
      hint: context.l10n.dayReviewCorrectHint,
      quote: quote,
    );
    if (text == null || text.isEmpty) return;
    _applyReview(await addDayReviewCorrection(
      widget.date,
      type: DayReviewCorrectionType.highlight,
      quote: quote,
      start: start,
      end: end,
      text: text,
    ));
  }

  Future<void> _redactSelection(int start, int end, String quote) async {
    _applyReview(await addDayReviewCorrection(
      widget.date,
      type: DayReviewCorrectionType.redaction,
      quote: quote,
      start: start,
      end: end,
    ));
  }

  Future<void> _deleteCorrection(DayReviewCorrection c) async {
    _applyReview(await deleteDayReviewCorrection(widget.date, c.id));
  }

  // -------------------------------------------------------------------- UI

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final review = _day?.review;
    return Scaffold(
      backgroundColor: OmiColors.surface0,
      appBar: AppBar(
        backgroundColor: OmiColors.surface0,
        title: Text(l10n.dayReviewTitle),
        actions: [
          if (review != null && review.finalText.isNotEmpty)
            IconButton(
              tooltip: l10n.dayReviewCopy,
              icon: const Icon(Icons.copy_rounded),
              onPressed: () {
                Clipboard.setData(ClipboardData(text: '${review.title}\n\n${review.finalText}'));
                AppSnackbar.showSnackbar(l10n.dayReviewCopied);
              },
            ),
          if (review != null)
            PopupMenuButton<String>(
              onSelected: (value) {
                if (value == 'reopen') _reopen();
              },
              itemBuilder: (_) => [PopupMenuItem(value: 'reopen', child: Text(l10n.dayReviewReopenAction))],
            ),
        ],
      ),
      body: Stack(
        children: [
          if (_error != null)
            Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(_error!, textAlign: TextAlign.center, style: TextStyle(color: OmiColors.textSecondary)),
                    const SizedBox(height: 12),
                    TextButton(onPressed: _load, child: Text(l10n.retry)),
                  ],
                ),
              ),
            )
          else if (_day == null)
            const Center(child: CircularProgressIndicator())
          else if (review == null)
            _buildSortView(_day!)
          else
            _buildReviewView(_day!, review),
          if (_busyMessage != null)
            Container(
              color: Colors.black54,
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const CircularProgressIndicator(),
                    const SizedBox(height: 16),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 32),
                      child: Text(_busyMessage!,
                          textAlign: TextAlign.center, style: TextStyle(color: OmiColors.textPrimary)),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  String _timeRange(DayReviewConversation c) {
    final f = DateFormat.jm();
    if (c.startedAt == null) return '';
    return c.finishedAt == null ? f.format(c.startedAt!) : '${f.format(c.startedAt!)} – ${f.format(c.finishedAt!)}';
  }

  Widget _conversationTile(DayReviewConversation c, {Widget? trailing, Color? color}) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: color ?? OmiColors.surface1, borderRadius: BorderRadius.circular(14)),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(c.emoji.isEmpty ? '💬' : c.emoji, style: const TextStyle(fontSize: 22)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(c.title.isEmpty ? context.l10n.dayReviewUntitled : c.title,
                    style: TextStyle(color: OmiColors.textPrimary, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(_timeRange(c), style: TextStyle(color: OmiColors.textSecondary, fontSize: 12)),
                if (c.overview.isNotEmpty || c.transcriptSnippet.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(c.overview.isNotEmpty ? c.overview : c.transcriptSnippet,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: OmiType.footnote.copyWith(
                          color: OmiColors.textSecondary,
                          fontStyle: c.overview.isEmpty ? FontStyle.italic : FontStyle.normal)),
                ],
              ],
            ),
          ),
          if (trailing != null) trailing,
        ],
      ),
    );
  }

  Widget _sortTile(DayReviewConversation c) {
    final l10n = context.l10n;
    final kept = _keep.contains(c.id);
    final dropped = _drop.contains(c.id);
    return Opacity(
      opacity: dropped ? 0.45 : 1,
      child: _conversationTile(
        c,
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Checkbox(
              value: _selected.contains(c.id),
              onChanged: dropped ? null : (_) => _toggleSelected(c.id),
              visualDensity: VisualDensity.compact,
              side: BorderSide(color: OmiColors.textSecondary),
            ),
            IconButton(
              tooltip: l10n.dayReviewKeepTooltip,
              visualDensity: VisualDensity.compact,
              icon: Icon(kept ? Icons.star_rounded : Icons.star_outline_rounded,
                  color: kept ? OmiColors.warning : OmiColors.textSecondary),
              onPressed: () => _toggleKeep(c.id),
            ),
            IconButton(
              tooltip: dropped ? l10n.dayReviewRestoreTooltip : l10n.dayReviewDropTooltip,
              visualDensity: VisualDensity.compact,
              icon: Icon(dropped ? Icons.restore_from_trash_outlined : Icons.delete_outline_rounded,
                  color: dropped ? OmiColors.textPrimary : OmiColors.textSecondary),
              onPressed: () => _toggleDrop(c.id),
            ),
          ],
        ),
      ),
    );
  }

  Widget _groupBlock(List<String> group, Map<String, DayReviewConversation> byId) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(8, 6, 8, 0),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(OmiRadius.lg),
        border: Border.all(color: OmiColors.surface3),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.call_merge_rounded, size: 18, color: OmiColors.textSecondary),
              const SizedBox(width: 6),
              Expanded(
                child: Text(context.l10n.dayReviewMergeGroupLabel,
                    style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
              ),
              TextButton(onPressed: () => _ungroup(group), child: Text(context.l10n.dayReviewUngroup)),
            ],
          ),
          for (final id in group)
            if (byId[id] != null) _conversationTile(byId[id]!),
        ],
      ),
    );
  }

  Widget _sectionTitle(String text) => Padding(
        padding: const EdgeInsets.only(top: 12, bottom: 8),
        child: Text(text, style: OmiType.headline.copyWith(color: OmiColors.textPrimary)),
      );

  Widget _buildSortView(DayReviewDay day) {
    final l10n = context.l10n;
    final byId = {for (final c in day.conversations) c.id: c};
    final grouped = _grouped;
    final loose = day.conversations.where((c) => !grouped.contains(c.id)).toList();
    final clear = loose.where((c) => !c.unclear).toList();
    final unclear = loose.where((c) => c.unclear).toList();
    final rolled = _rolledCount;
    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            children: [
              Text(_prettyDate,
                  style: TextStyle(color: OmiColors.textPrimary, fontSize: 20, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text(l10n.dayReviewBundleHint, style: TextStyle(color: OmiColors.textSecondary, height: 1.4)),
              if (day.conversations.isNotEmpty) ...[
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    key: const Key('day_review_bundle'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: OmiColors.textPrimary,
                      foregroundColor: Colors.black,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(OmiRadius.lg)),
                    ),
                    onPressed: _bundle,
                    icon: const Icon(Icons.auto_awesome_rounded, size: 18),
                    label: Text(l10n.dayReviewBundleButton),
                  ),
                ),
                const SizedBox(height: 16),
                Text(l10n.dayReviewOrSortManually,
                    style: OmiType.footnote.copyWith(color: OmiColors.textSecondary, fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
                Text(day.holdEnabled ? l10n.dayReviewSortHintHold : l10n.dayReviewSortHint,
                    style: OmiType.footnote.copyWith(color: OmiColors.textSecondary, height: 1.4)),
              ],
              if (day.processingCount > 0) ...[
                const SizedBox(height: 8),
                Text(l10n.dayReviewProcessing(day.processingCount),
                    style: TextStyle(color: OmiColors.textSecondary, fontSize: 12.5)),
              ],
              if (day.droppedAutoCount > 0) ...[
                const SizedBox(height: 4),
                Text(l10n.dayReviewAutoDropped(day.droppedAutoCount),
                    style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
              ],
              const SizedBox(height: 16),
              if (day.conversations.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 40),
                  child: Text(l10n.dayReviewEmpty,
                      textAlign: TextAlign.center, style: TextStyle(color: OmiColors.textSecondary)),
                ),
              for (final g in _groups) _groupBlock(g, byId),
              for (final c in clear) _sortTile(c),
              if (unclear.isNotEmpty) ...[
                _sectionTitle(l10n.dayReviewUnclearTitle),
                for (final c in unclear) _sortTile(c),
              ],
            ],
          ),
        ),
        if (_selected.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
            child: SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: OmiColors.textPrimary,
                  side: BorderSide(color: OmiColors.textSecondary),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(OmiRadius.md)),
                ),
                onPressed: _selected.length < 2 ? null : _mergeSelected,
                icon: const Icon(Icons.call_merge_rounded),
                label: Text(l10n.dayReviewMergeSelected(_selected.length)),
              ),
            ),
          ),
        if (day.conversations.isNotEmpty)
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: OmiColors.textPrimary,
                    foregroundColor: Colors.black,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                  ),
                  onPressed: _finalize,
                  child: Text(_groups.isEmpty && _drop.isEmpty
                      ? l10n.dayReviewFinalizeButton(_keep.length, rolled)
                      : l10n.dayReviewFinalizeButtonFull(_keep.length, _groups.length, _drop.length, rolled)),
                ),
              ),
            ),
          ),
      ],
    );
  }

  Widget _paragraphBlock(DayReview review, SummaryParagraph paragraph) {
    final spans = paragraph.segments.map((s) {
      switch (s.kind) {
        case SummarySegmentKind.redaction:
          // Same characters (offsets stay exact), drawn as a bar.
          return TextSpan(
            text: s.text,
            style: TextStyle(color: Colors.transparent, backgroundColor: OmiColors.surface3),
          );
        case SummarySegmentKind.highlight:
          return TextSpan(
            text: s.text,
            style: TextStyle(backgroundColor: OmiColors.warning.withValues(alpha: 0.35), color: OmiColors.textPrimary),
          );
        case SummarySegmentKind.plain:
          return TextSpan(text: s.text);
      }
    }).toList();

    return SelectableText.rich(
      TextSpan(children: spans, style: TextStyle(color: OmiColors.textPrimary, fontSize: 16, height: 1.55)),
      contextMenuBuilder: (context, editable) {
        final selection = editable.textEditingValue.selection;
        final items = <ContextMenuButtonItem>[...editable.contextMenuButtonItems];
        if (selection.isValid && !selection.isCollapsed) {
          final start = paragraph.start + selection.start;
          final end = paragraph.start + selection.end;
          final quote = review.summaryText.substring(start, end);
          void done() {
            editable.hideToolbar();
            editable.userUpdateTextEditingValue(
              editable.textEditingValue.copyWith(selection: TextSelection.collapsed(offset: selection.end)),
              SelectionChangedCause.toolbar,
            );
          }

          items.insertAll(0, [
            ContextMenuButtonItem(
              label: context.l10n.dayReviewCorrectAction,
              onPressed: () {
                done();
                _correctSelection(start, end, quote);
              },
            ),
            ContextMenuButtonItem(
              label: context.l10n.dayReviewRedactAction,
              onPressed: () {
                done();
                _redactSelection(start, end, quote);
              },
            ),
          ]);
        }
        return AdaptiveTextSelectionToolbar.buttonItems(anchors: editable.contextMenuAnchors, buttonItems: items);
      },
    );
  }

  Widget _userParagraph(DayReviewCorrection c) {
    return Container(
      margin: const EdgeInsets.only(top: 10),
      padding: const EdgeInsets.fromLTRB(12, 10, 4, 10),
      decoration: BoxDecoration(
        color: OmiColors.surface1,
        borderRadius: BorderRadius.circular(12),
        border: Border(left: BorderSide(color: OmiColors.textSecondary, width: 3)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(context.l10n.dayReviewYourParagraph,
                    style: TextStyle(color: OmiColors.textSecondary, fontSize: 11.5)),
                const SizedBox(height: 4),
                Text(c.text ?? '', style: TextStyle(color: OmiColors.textPrimary, fontSize: 15.5, height: 1.5)),
              ],
            ),
          ),
          IconButton(
            icon: Icon(Icons.close, size: 18, color: OmiColors.textSecondary),
            onPressed: () => _deleteCorrection(c),
          ),
        ],
      ),
    );
  }

  Widget _addParagraphButton(int? after) {
    return Align(
      alignment: Alignment.centerLeft,
      child: TextButton.icon(
        style: TextButton.styleFrom(foregroundColor: OmiColors.textSecondary, padding: EdgeInsets.zero),
        onPressed: () => _addParagraph(after),
        icon: const Icon(Icons.add, size: 16),
        label: Text(context.l10n.dayReviewAddParagraph, style: const TextStyle(fontSize: 12.5)),
      ),
    );
  }

  Widget _correctionRow(DayReviewCorrection c) {
    final l10n = context.l10n;
    final String label;
    switch (c.type) {
      case DayReviewCorrectionType.highlight:
        label = '“${c.quote}” → ${c.text}';
        break;
      case DayReviewCorrectionType.redaction:
        label = l10n.dayReviewRedactedQuote(c.quote ?? '');
        break;
      case DayReviewCorrectionType.paragraph:
        label = c.text ?? '';
        break;
    }
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        c.type == DayReviewCorrectionType.redaction
            ? Icons.block
            : (c.type == DayReviewCorrectionType.highlight ? Icons.edit_note : Icons.notes),
        color: OmiColors.textSecondary,
        size: 20,
      ),
      title: Text(label, maxLines: 3, overflow: TextOverflow.ellipsis, style: TextStyle(color: OmiColors.textPrimary)),
      subtitle: Text(c.isApplied ? l10n.dayReviewCorrectionApplied : l10n.dayReviewCorrectionPending,
          style: TextStyle(color: OmiColors.textSecondary, fontSize: 11.5)),
      trailing: IconButton(
        icon: Icon(Icons.delete_outline, color: OmiColors.textSecondary, size: 20),
        onPressed: () => _deleteCorrection(c),
      ),
    );
  }

  Widget _tagChip(String tag) => Container(
        padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.xs, vertical: OmiSpacing.xxs),
        decoration: BoxDecoration(color: OmiColors.chipSurface, borderRadius: BorderRadius.circular(OmiRadius.md)),
        child: Text('#$tag', style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
      );

  /// The conversations a section covers, in the order the day had them.
  List<DayReviewConversation> _sectionClips(DayReviewSection section, Map<String, DayReviewConversation> byId) => [
        for (final id in section.conversationIds)
          if (byId[id] != null) byId[id]!
      ];

  /// "7:48 AM – 9:16 AM" across a section's conversations, or '' when none have times.
  String _spanLabel(List<DayReviewConversation> clips) {
    DateTime? first;
    DateTime? last;
    for (final c in clips) {
      final start = c.startedAt;
      final end = c.finishedAt ?? c.startedAt;
      if (start != null && (first == null || start.isBefore(first))) first = start;
      if (end != null && (last == null || end.isAfter(last))) last = end;
    }
    if (first == null || last == null) return '';
    final fmt = DateFormat.jm();
    return '${fmt.format(first.toLocal())} – ${fmt.format(last.toLocal())}';
  }

  Widget _sectionCard(
    DayReview review,
    DayReviewSection? section,
    List<SummaryParagraph> paragraphs,
    Map<String, DayReviewConversation> byId,
    List<DayReviewCorrection> Function(int? index) anchoredAt,
  ) {
    final l10n = context.l10n;
    final clips = section == null ? const <DayReviewConversation>[] : _sectionClips(section, byId);
    final open = section != null && _openSections.contains(section.id);
    final favorited = section?.favorited ?? false;
    final span = _spanLabel(clips);
    return Container(
      key: section == null ? null : Key('day_review_section_${section.id}'),
      margin: const EdgeInsets.only(bottom: OmiSpacing.sm),
      padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.sm, OmiSpacing.xs, OmiSpacing.xs),
      decoration: BoxDecoration(
        color: OmiColors.surface1,
        borderRadius: OmiRadius.lgAll,
        border: favorited ? Border.all(color: OmiColors.warning.withValues(alpha: 0.6)) : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (span.isNotEmpty || clips.length > 1)
            Padding(
              padding: const EdgeInsets.only(right: OmiSpacing.xs),
              child: Row(
                children: [
                  if (span.isNotEmpty) Text(span, style: OmiType.caption.copyWith(color: OmiColors.textSecondary)),
                  const Spacer(),
                  if (clips.length > 1)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.xs, vertical: 2),
                      decoration: BoxDecoration(color: OmiColors.chipSurface, borderRadius: OmiRadius.mdAll),
                      child: Text(l10n.dayReviewSmallMoments(clips.length),
                          style: OmiType.caption.copyWith(color: OmiColors.textSecondary)),
                    ),
                ],
              ),
            ),
          if (section != null && section.title.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: OmiSpacing.xxs, bottom: OmiSpacing.xs, right: OmiSpacing.xs),
              child: Text(section.title, style: OmiType.headline.copyWith(color: OmiColors.textPrimary)),
            ),
          for (final p in paragraphs) ...[
            Padding(padding: const EdgeInsets.only(right: OmiSpacing.xs), child: _paragraphBlock(review, p)),
            for (final c in anchoredAt(p.index)) _userParagraph(c),
            const SizedBox(height: OmiSpacing.xs),
          ],
          if (section != null && section.tags.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: OmiSpacing.xxs),
              child: Wrap(spacing: 6, runSpacing: 4, children: [for (final t in section.tags) _tagChip(t)]),
            ),
          if (favorited)
            Text(l10n.dayReviewSectionFavorited(clips.length),
                style: OmiType.footnote.copyWith(color: OmiColors.warning)),
          Row(
            children: [
              if (clips.isNotEmpty)
                TextButton.icon(
                  key: section == null ? null : Key('day_review_section_clips_${section.id}'),
                  style: TextButton.styleFrom(
                    foregroundColor: OmiColors.textSecondary,
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                  ),
                  onPressed: () {
                    final id = section?.id;
                    if (id == null) return;
                    setState(() => open ? _openSections.remove(id) : _openSections.add(id));
                  },
                  icon: Icon(open ? Icons.expand_less_rounded : Icons.expand_more_rounded, size: 18),
                  label: Text(open ? l10n.dayReviewHideClips : l10n.dayReviewSectionClips(clips.length),
                      style: OmiType.footnote),
                ),
              const Spacer(),
              IconButton(
                tooltip: l10n.dayReviewAddParagraphHere,
                visualDensity: VisualDensity.compact,
                icon: Icon(Icons.add_rounded, size: 20, color: OmiColors.textSecondary),
                onPressed: () => _addParagraph(paragraphs.isEmpty ? null : paragraphs.last.index),
              ),
              if (section != null && clips.isNotEmpty)
                IconButton(
                  key: Key('day_review_section_favorite_${section.id}'),
                  tooltip: favorited ? l10n.dayReviewUnfavoriteSection : l10n.dayReviewFavoriteSection,
                  visualDensity: VisualDensity.compact,
                  icon: Icon(favorited ? Icons.star_rounded : Icons.star_outline_rounded,
                      size: 22, color: favorited ? OmiColors.warning : OmiColors.textSecondary),
                  onPressed: () => _favoriteSection(section, !favorited),
                ),
            ],
          ),
          if (open) ...[
            const SizedBox(height: OmiSpacing.xxs),
            for (final c in clips)
              Padding(
                padding: const EdgeInsets.only(right: OmiSpacing.xs),
                child: _conversationTile(c, color: OmiColors.surface2),
              ),
          ],
        ],
      ),
    );
  }

  Widget _group(String title, List<Widget> children, {bool initiallyExpanded = false}) => Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: EdgeInsets.zero,
          initiallyExpanded: initiallyExpanded,
          iconColor: OmiColors.textSecondary,
          collapsedIconColor: OmiColors.textSecondary,
          title: Text(title, style: OmiType.subhead.copyWith(color: OmiColors.textPrimary)),
          children: children,
        ),
      );

  Widget _buildReviewView(DayReviewDay day, DayReview review) {
    final l10n = context.l10n;
    final paragraphs = buildSummaryParagraphs(review.summaryText, review.corrections);
    final pendingParagraphs =
        review.corrections.where((c) => c.type == DayReviewCorrectionType.paragraph && !c.isApplied).toList();
    List<DayReviewCorrection> anchoredAt(int? index) => pendingParagraphs
        .where((c) => index == null
            ? (c.afterParagraph == null || c.afterParagraph! >= paragraphs.length)
            : c.afterParagraph == index)
        .toList();
    final byId = {for (final c in day.conversations) c.id: c};
    final kept = day.conversations.where((c) => review.keptConversationIds.contains(c.id)).toList();
    final rolled = day.conversations.where((c) => review.rolledUpConversationIds.contains(c.id)).toList();
    final dropped = day.conversations.where((c) => review.droppedConversationIds.contains(c.id)).toList();
    final merged = review.mergedGroups.length;

    // One card per section: the paragraphs from where a section starts up to the next one.
    // Text before any section (a review written before sections existed) is a card without a title.
    final cards = <(DayReviewSection?, List<SummaryParagraph>)>[];
    for (final p in paragraphs) {
      final starting = sectionsStartingAt(review.sections, p.start);
      if (starting.isNotEmpty || cards.isEmpty) cards.add((starting.isEmpty ? null : starting.last, []));
      cards.last.$2.add(p);
    }
    final placed = {
      for (final c in cards)
        if (c.$1 != null) c.$1!.id
    };
    for (final s in review.sections) {
      if (!placed.contains(s.id) && s.conversationIds.isNotEmpty) cards.add((s, []));
    }

    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xs, OmiSpacing.md, OmiSpacing.xl),
            children: [
              Text(_prettyDate, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
              const SizedBox(height: OmiSpacing.xxs),
              Text(review.title, style: OmiType.title2.copyWith(color: OmiColors.textPrimary)),
              const SizedBox(height: OmiSpacing.xxs),
              Text(
                  merged == 0 && dropped.isEmpty
                      ? l10n.dayReviewCounts(rolled.length, kept.length)
                      : l10n.dayReviewCountsFull(rolled.length, kept.length, merged, dropped.length),
                  style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
              if (paragraphs.isNotEmpty) ...[
                const SizedBox(height: OmiSpacing.xxs),
                Text(l10n.dayReviewEditHint, style: OmiType.caption.copyWith(color: OmiColors.textSecondary)),
              ],
              const SizedBox(height: OmiSpacing.md),
              if (kept.isNotEmpty) ...[
                _group(l10n.dayReviewKeptTitle(kept.length), [for (final c in kept) _conversationTile(c)],
                    initiallyExpanded: kept.length <= 3),
                const SizedBox(height: OmiSpacing.xs),
              ],
              for (final card in cards) _sectionCard(review, card.$1, card.$2, byId, anchoredAt),
              for (final c in anchoredAt(null)) _userParagraph(c),
              if (paragraphs.isEmpty) _addParagraphButton(null),
              if (review.tags.isNotEmpty) ...[
                const SizedBox(height: OmiSpacing.md),
                Text(l10n.dayReviewTagsTitle, style: OmiType.subhead.copyWith(color: OmiColors.textPrimary)),
                const SizedBox(height: OmiSpacing.xs),
                Wrap(spacing: 6, runSpacing: 6, children: [for (final t in review.tags) _tagChip(t)]),
              ],
              if (review.corrections.isNotEmpty) ...[
                const SizedBox(height: OmiSpacing.md),
                Text(l10n.dayReviewCorrectionsTitle, style: OmiType.subhead.copyWith(color: OmiColors.textPrimary)),
                for (final c in review.corrections) _correctionRow(c),
              ],
              const SizedBox(height: OmiSpacing.md),
              if (rolled.isNotEmpty)
                _group(l10n.dayReviewRolledTitle(rolled.length), [for (final c in rolled) _conversationTile(c)]),
              if (merged > 0)
                _group(l10n.dayReviewMergedTitle(merged), [
                  // The sources were merged away; only their count and ids remain.
                  for (final g in review.mergedGroups)
                    ListTile(
                      dense: true,
                      leading: Icon(Icons.call_merge_rounded, color: OmiColors.textSecondary),
                      title: Text(l10n.dayReviewMergeGroupLabel,
                          style: OmiType.subhead.copyWith(color: OmiColors.textPrimary)),
                      subtitle: Text('${g.length}', style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                    ),
                ]),
              if (dropped.isNotEmpty)
                _group(l10n.dayReviewDroppedTitle(dropped.length), [for (final c in dropped) _conversationTile(c)]),
            ],
          ),
        ),
        if (review.pendingCorrectionCount > 0)
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xxs, OmiSpacing.md, OmiSpacing.sm),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: OmiColors.accent,
                    foregroundColor: OmiColors.surface0,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: const RoundedRectangleBorder(borderRadius: OmiRadius.lgAll),
                  ),
                  onPressed: _regenerate,
                  child: Text(l10n.dayReviewApplyButton(review.pendingCorrectionCount)),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
