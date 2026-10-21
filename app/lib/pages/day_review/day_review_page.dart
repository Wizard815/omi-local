import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/day_review/summary_segments.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// End-of-day review for one local date.
///
/// Before review: star the conversations to keep; everything else is rolled
/// into one long day summary (and leaves the conversation list).
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
          ..addAll(_day!.conversations.where((c) => c.starred && !c.rolledUp).map((c) => c.id));
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

  Future<void> _finalize() async {
    final l10n = context.l10n;
    final day = _day!;
    final rolled = day.conversations.where((c) => !_keep.contains(c.id)).length;
    if (rolled > 0) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.dayReviewConfirmTitle),
          content: Text(l10n.dayReviewConfirmBody(rolled)),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.dayReviewConfirmAction)),
          ],
        ),
      );
      if (confirmed != true) return;
    }
    await _busy(l10n.dayReviewWriting, () async {
      final result = await finalizeDayReview(widget.date, _keep.toList());
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
                child: Text('“$quote”', style: TextStyle(color: Colors.grey.shade400, fontStyle: FontStyle.italic)),
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
      backgroundColor: Theme.of(context).colorScheme.primary,
      appBar: AppBar(
        backgroundColor: Theme.of(context).colorScheme.primary,
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
                    Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white70)),
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
                      child:
                          Text(_busyMessage!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white)),
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

  Widget _conversationTile(DayReviewConversation c, {Widget? trailing}) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: const Color(0xFF1F1F25), borderRadius: BorderRadius.circular(14)),
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
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(_timeRange(c), style: TextStyle(color: Colors.grey.shade500, fontSize: 12)),
                if (c.overview.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(c.overview,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: Colors.grey.shade400, fontSize: 13)),
                ],
              ],
            ),
          ),
          if (trailing != null) trailing,
        ],
      ),
    );
  }

  Widget _buildSortView(DayReviewDay day) {
    final l10n = context.l10n;
    final rolled = day.conversations.where((c) => !_keep.contains(c.id)).length;
    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            children: [
              Text(_prettyDate, style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              Text(l10n.dayReviewSortHint, style: TextStyle(color: Colors.grey.shade400, height: 1.4)),
              if (day.processingCount > 0) ...[
                const SizedBox(height: 8),
                Text(l10n.dayReviewProcessing(day.processingCount),
                    style: TextStyle(color: Colors.grey.shade500, fontSize: 12.5)),
              ],
              const SizedBox(height: 16),
              if (day.conversations.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 40),
                  child: Text(l10n.dayReviewEmpty,
                      textAlign: TextAlign.center, style: const TextStyle(color: Colors.white70)),
                ),
              for (final c in day.conversations)
                _conversationTile(
                  c,
                  trailing: IconButton(
                    tooltip: l10n.dayReviewKeepTooltip,
                    icon: Icon(
                      _keep.contains(c.id) ? Icons.star_rounded : Icons.star_outline_rounded,
                      color: _keep.contains(c.id) ? Colors.amber : Colors.grey.shade500,
                    ),
                    onPressed: () => setState(() => _keep.contains(c.id) ? _keep.remove(c.id) : _keep.add(c.id)),
                  ),
                ),
            ],
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
                    backgroundColor: Colors.white,
                    foregroundColor: Colors.black,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                  ),
                  onPressed: _finalize,
                  child: Text(l10n.dayReviewFinalizeButton(_keep.length, rolled)),
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
            style: const TextStyle(color: Colors.transparent, backgroundColor: Color(0xFF4A4A52)),
          );
        case SummarySegmentKind.highlight:
          return TextSpan(
            text: s.text,
            style: TextStyle(backgroundColor: Colors.amber.withValues(alpha: 0.35), color: Colors.white),
          );
        case SummarySegmentKind.plain:
          return TextSpan(text: s.text);
      }
    }).toList();

    return SelectableText.rich(
      TextSpan(children: spans, style: const TextStyle(color: Colors.white, fontSize: 16, height: 1.55)),
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
        color: const Color(0xFF1F1F25),
        borderRadius: BorderRadius.circular(12),
        border: const Border(left: BorderSide(color: Colors.white54, width: 3)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(context.l10n.dayReviewYourParagraph,
                    style: TextStyle(color: Colors.grey.shade500, fontSize: 11.5)),
                const SizedBox(height: 4),
                Text(c.text ?? '', style: const TextStyle(color: Colors.white, fontSize: 15.5, height: 1.5)),
              ],
            ),
          ),
          IconButton(
            icon: const Icon(Icons.close, size: 18, color: Colors.white54),
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
        style: TextButton.styleFrom(foregroundColor: Colors.grey.shade500, padding: EdgeInsets.zero),
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
        color: Colors.white70,
        size: 20,
      ),
      title: Text(label, maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white)),
      subtitle: Text(c.isApplied ? l10n.dayReviewCorrectionApplied : l10n.dayReviewCorrectionPending,
          style: TextStyle(color: Colors.grey.shade500, fontSize: 11.5)),
      trailing: IconButton(
        icon: const Icon(Icons.delete_outline, color: Colors.white54, size: 20),
        onPressed: () => _deleteCorrection(c),
      ),
    );
  }

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
    final kept = day.conversations.where((c) => review.keptConversationIds.contains(c.id)).toList();
    final rolled = day.conversations.where((c) => review.rolledUpConversationIds.contains(c.id)).toList();

    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
            children: [
              Text(_prettyDate, style: TextStyle(color: Colors.grey.shade500, fontSize: 13)),
              const SizedBox(height: 4),
              Text(review.title,
                  style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w700)),
              const SizedBox(height: 6),
              Text(l10n.dayReviewCounts(rolled.length, kept.length),
                  style: TextStyle(color: Colors.grey.shade500, fontSize: 12.5)),
              if (paragraphs.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(l10n.dayReviewEditHint, style: TextStyle(color: Colors.grey.shade600, fontSize: 12)),
              ],
              const SizedBox(height: 16),
              for (final p in paragraphs) ...[
                _paragraphBlock(review, p),
                for (final c in anchoredAt(p.index)) _userParagraph(c),
                _addParagraphButton(p.index),
                const SizedBox(height: 4),
              ],
              for (final c in anchoredAt(null)) _userParagraph(c),
              if (paragraphs.isEmpty) _addParagraphButton(null),
              if (review.corrections.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text(l10n.dayReviewCorrectionsTitle,
                    style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600)),
                for (final c in review.corrections) _correctionRow(c),
              ],
              const SizedBox(height: 16),
              if (kept.isNotEmpty)
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: Text(l10n.dayReviewKeptTitle(kept.length), style: const TextStyle(color: Colors.white)),
                  children: [for (final c in kept) _conversationTile(c)],
                ),
              if (rolled.isNotEmpty)
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: Text(l10n.dayReviewRolledTitle(rolled.length), style: const TextStyle(color: Colors.white)),
                  children: [for (final c in rolled) _conversationTile(c)],
                ),
            ],
          ),
        ),
        if (review.pendingCorrectionCount > 0)
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.white,
                    foregroundColor: Colors.black,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
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
