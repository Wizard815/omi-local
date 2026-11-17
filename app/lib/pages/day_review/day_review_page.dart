import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/pages/approvals/waiting_tasks_section.dart';
import 'package:omi/pages/chat/widgets/content_blocks/conversation_link_blocks.dart';
import 'package:omi/pages/connections/send_to_agent_sheet.dart';
import 'package:omi/pages/connections/send_to_wiki_sheet.dart';
import 'package:omi/providers/waiting_approval_controller.dart';
import 'package:omi/pages/action_items/day_tasks_page.dart';
import 'package:omi/pages/day_review/day_review_edit_text_sheet.dart';
import 'package:omi/pages/day_review/day_review_text_sheet.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/day_review/summary_segments.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// End-of-day review for one local date.
///
/// Before review: star the conversations to keep, tick clips to merge them
/// into one, drop the noise; everything else is rolled into one long day
/// summary (and leaves the conversation list). On a self-hosted server the
/// clips arrive unprocessed (transcript only) and only what you keep or merge
/// gets the full treatment.
/// After: the summary reads like a doc you can correct — select text to attach
/// a correction or redact it, add whole paragraphs, then "Apply corrections" — or
/// "Edit text" to change the title and each section's words directly (with Undo).
/// "From this day" links to the day's tasks and the calendar check, so the
/// tasks and events Omi heard are reviewed in the same pass.
class DayReviewPage extends StatefulWidget {
  final String date;

  /// The day-review server calls; tests pass a fake.
  final DayReviewApi api;

  /// omi-local: the day's tasks Omi heard wait for the user's OK in "From this day".
  final ApprovalsApi approvals;

  /// omi-local: Connections, for "Send to wiki" on kept clips when the user chose to be asked.
  final ConnectionsApi connections;

  /// Opens one clip's conversation (title, summary, transcript and action items are editable
  /// there); tests pass a recorder.
  final Future<void> Function(BuildContext context, String conversationId)? openConversation;

  const DayReviewPage(
      {super.key,
      required this.date,
      this.api = const DayReviewApi(),
      this.approvals = const ApprovalsApi(),
      this.connections = const ConnectionsApi(),
      this.openConversation});

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

  late final WaitingApprovalController _waiting = WaitingApprovalController(api: widget.approvals);

  /// The wiki connection, when it offers "Send to wiki" on kept clips (mode "ask").
  bool _offerWikiSend = false;

  /// A clip whose keep is being saved.
  String? _keeping;

  @override
  void initState() {
    super.initState();
    _load();
    _waiting.load();
    _loadConnections();
  }

  Future<void> _loadConnections() async {
    final ConnectionResult<ConnectionsState> result;
    try {
      result = await widget.connections.load();
    } catch (_) {
      return; // Only decides whether kept clips offer Send to wiki; the review works without it.
    }
    if (!mounted || result.value == null) return;
    final wiki = result.value!['wiki'];
    setState(() => _offerWikiSend = wiki.ready && wiki.text('send_kept_conversations') == 'ask');
  }

  Future<void> _openConversation(String id) async {
    final open = widget.openConversation;
    if (open != null) return open(context, id);
    final found = await openChatBlockConversation(context, conversationId: id);
    if (!found && mounted) AppSnackbar.showSnackbarError(context.l10n.dayReviewConversationGone);
  }

  /// Keep one clip of the summary as its own conversation, or fold it back in.
  Future<void> _keepConversation(String id, bool kept) async {
    setState(() => _keeping = id);
    final result = await widget.api.keepConversation(widget.date, id, kept);
    if (!mounted) return;
    setState(() => _keeping = null);
    if (result.ok) {
      AppSnackbar.showSnackbar(kept ? context.l10n.dayReviewClipKept : context.l10n.dayReviewClipFolded);
      await _load();
    } else {
      _applyReview(result);
    }
  }

  void _sendSection(DayReviewSection section) {
    showSendToAgentSheet(
      context,
      jobContext: AgentJobContext(sectionDate: widget.date, sectionId: section.id),
      api: widget.connections,
    );
  }

  @override
  void dispose() {
    _waiting.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final result = await widget.api.getDay(widget.date);
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
  /// It is the same finalize as the bottom button, so the stars, merges and drops made on this
  /// page count (the server's bundle reads only stars already saved, and would roll up a
  /// conversation starred here a moment ago).
  Future<void> _bundle() => _finalize(busyMessage: context.l10n.dayReviewBundleWriting);

  Future<void> _favoriteSection(DayReviewSection section, bool favorited) async {
    final result = await widget.api.favoriteSection(widget.date, section.id, favorited);
    if (!mounted) return;
    if (result.ok) {
      await _load();
    } else {
      _applyReview(result);
    }
  }

  Future<void> _finalize({String? busyMessage}) async {
    final l10n = context.l10n;
    final rolled = _rolledCount;
    if (rolled > 0 || _groups.isNotEmpty || _drop.isNotEmpty) {
      final extra = _groups.isNotEmpty || _drop.isNotEmpty
          ? '\n\n${l10n.dayReviewConfirmExtra(_groups.length, _drop.length)}'
          : '';
      final confirmed = await showOmiConfirm(
        context,
        title: l10n.dayReviewConfirmTitle,
        message: '${l10n.dayReviewConfirmBody(rolled)}$extra',
        confirmLabel: l10n.dayReviewConfirmAction,
      );
      if (!confirmed || !mounted) return;
    }
    await _busy(busyMessage ?? l10n.dayReviewWriting, () async {
      final result = await widget.api.finalize(
        widget.date,
        _keep.toList(),
        mergeGroups: _groups.map((g) => List<String>.from(g)).toList(),
        drop: _drop.toList(),
      );
      if (result.ok) {
        await _load();
      } else if (mounted) {
        AppSnackbar.showSnackbarError(result.error ?? l10n.dayReviewActionFailed);
      }
    });
  }

  /// A rewrite replaces what the user typed by hand, so it asks first when there is any.
  Future<bool> _confirmReplaceEdits({String? message}) {
    final l10n = context.l10n;
    return showOmiConfirm(
      context,
      title: l10n.dayReviewRewriteEditsTitle,
      message: message ?? l10n.dayReviewRewriteEditsBody,
      confirmLabel: l10n.dayReviewRewriteEditsAction,
      destructive: true,
    );
  }

  Future<void> _regenerate() async {
    final edited = _day?.review?.editedByUser ?? false;
    if (edited && !await _confirmReplaceEdits()) return;
    if (!mounted) return;
    await _busy(context.l10n.dayReviewApplying, () async {
      _applyReview(await widget.api.regenerate(widget.date, replaceEdits: edited));
    });
  }

  /// "Edit text": the user's own words for the title and each section; Undo puts the previous back.
  Future<void> _editText() async {
    final review = _day?.review;
    if (review == null) return;
    final edit = await showDayReviewEditTextSheet(context, review);
    if (edit == null || !mounted) return;
    final result = await widget.api.editText(
      widget.date,
      title: edit.title,
      sections: edit.sections,
      summaryText: edit.summaryText,
    );
    _applyReview(result);
    if (!result.ok || !mounted) return;
    final undo = edit.revertOn(review);
    OmiFeedback.undo(context, context.l10n.dayReviewTextSaved, icon: Icons.edit_outlined, onUndo: () async {
      _applyReview(await widget.api.editText(
        widget.date,
        title: undo.title,
        sections: undo.sections,
        summaryText: undo.summaryText,
      ));
    });
  }

  /// The user's OK on a draft: the day is finalized as the draft says (no new model call unless the
  /// day changed), and only now does its summary go to the calendar.
  Future<void> _approve({bool replaceEdits = false}) async {
    final l10n = context.l10n;
    DayReviewResult<DayReview>? result;
    await _busy(l10n.approvalApproving, () async {
      result = await widget.api.approve(widget.date, replaceEdits: replaceEdits);
    });
    final outcome = result;
    if (outcome == null || !mounted) return;
    if (!outcome.ok && outcome.statusCode == 409 && !replaceEdits && (_day?.review?.editedByUser ?? false)) {
      // The day changed since the user edited the draft: approving means rewriting it.
      if (await _confirmReplaceEdits(message: l10n.dayReviewApproveRewriteBody) && mounted) {
        await _approve(replaceEdits: true);
      }
      return;
    }
    if (!outcome.ok) {
      AppSnackbar.showSnackbarError(outcome.error ?? l10n.dayReviewActionFailed);
      return;
    }
    OmiFeedback.confirm(context, l10n.approvalDayApproved);
    await _busy(l10n.approvalApproving, _load);
  }

  Future<void> _reopen() async {
    final l10n = context.l10n;
    final draft = _day?.review?.isDraft ?? false;
    final confirmed = await showOmiConfirm(
      context,
      title: draft ? l10n.approvalDraftSortTitle : l10n.dayReviewReopenTitle,
      message: draft ? l10n.approvalDraftSortBody : l10n.approvalReopenBody,
      confirmLabel: draft ? l10n.approvalDraftSortAction : l10n.dayReviewReopenAction,
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    await _busy(l10n.dayReviewReopening, () async {
      final result = await widget.api.reopen(widget.date);
      if (!result.ok) {
        if (mounted) AppSnackbar.showSnackbarError(result.error ?? l10n.dayReviewActionFailed);
        return;
      }
      await _load();
    });
  }

  Future<String?> _askText({required String title, String? hint, String? quote}) =>
      showDayReviewTextSheet(context, title: title, hint: hint, quote: quote);

  Future<void> _addParagraph(int? afterParagraph) async {
    final text =
        await _askText(title: context.l10n.dayReviewAddParagraphTitle, hint: context.l10n.dayReviewAddParagraphHint);
    if (text == null || text.isEmpty) return;
    _applyReview(await widget.api.addCorrection(
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
    _applyReview(await widget.api.addCorrection(
      widget.date,
      type: DayReviewCorrectionType.highlight,
      quote: quote,
      start: start,
      end: end,
      text: text,
    ));
  }

  Future<void> _redactSelection(int start, int end, String quote) async {
    _applyReview(await widget.api.addCorrection(
      widget.date,
      type: DayReviewCorrectionType.redaction,
      quote: quote,
      start: start,
      end: end,
    ));
  }

  /// Removing a correction takes back what the user wrote, so it is undoable (docs/ux-contract.md
  /// §4): Undo adds the same correction again, waiting to be applied.
  Future<void> _deleteCorrection(DayReviewCorrection c) async {
    final l10n = context.l10n;
    final result = await widget.api.deleteCorrection(widget.date, c.id);
    _applyReview(result);
    if (!result.ok || !mounted) return;
    OmiFeedback.undo(context, l10n.dayReviewCorrectionRemoved, onUndo: () async {
      _applyReview(await widget.api.addCorrection(
        widget.date,
        type: c.type,
        quote: c.quote,
        start: c.start,
        end: c.end,
        text: c.text,
        afterParagraph: c.afterParagraph,
      ));
    });
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
                if (value == 'edit_text') _editText();
              },
              itemBuilder: (_) => [
                if (review.summaryText.isNotEmpty)
                  PopupMenuItem(value: 'edit_text', child: Text(l10n.dayReviewEditText)),
                PopupMenuItem(
                  value: 'reopen',
                  child: Text(review.isDraft ? l10n.approvalDraftSortAction : l10n.dayReviewReopenAction),
                ),
              ],
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

  Widget _conversationTile(DayReviewConversation c, {Widget? trailing, Color? color, VoidCallback? onTap}) {
    final tile = Container(
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
    if (onTap == null) return tile;
    return InkWell(
      key: Key('day_review_open_${c.id}'),
      borderRadius: BorderRadius.circular(14),
      onTap: onTap,
      child: tile,
    );
  }

  /// A clip of the reviewed day: tap opens it; in a section, a star keeps it as its own conversation.
  Widget _reviewClip(DayReview review, DayReviewConversation c, {Color? color, bool keepButton = false}) {
    final l10n = context.l10n;
    final kept = review.keptConversationIds.contains(c.id);
    final trailing = <Widget>[
      if (kept && _offerWikiSend)
        IconButton(
          key: Key('day_review_send_wiki_${c.id}'),
          tooltip: l10n.sendToWikiTitle,
          visualDensity: VisualDensity.compact,
          icon: Icon(Icons.ios_share_rounded, size: 20, color: OmiColors.textSecondary),
          onPressed: () => showSendToWikiSheet(context, c.id, api: widget.connections),
        ),
      if (keepButton)
        IconButton(
          key: Key('day_review_keep_${c.id}'),
          tooltip: kept ? l10n.dayReviewFoldClip : l10n.dayReviewKeepClip,
          visualDensity: VisualDensity.compact,
          icon: Icon(kept ? Icons.star_rounded : Icons.star_outline_rounded,
              size: 22, color: kept ? OmiColors.warning : OmiColors.textSecondary),
          onPressed: _keeping == null ? () => _keepConversation(c.id, !kept) : null,
        ),
    ];
    return _conversationTile(
      c,
      color: color,
      onTap: () => _openConversation(c.id),
      trailing: trailing.isEmpty ? null : Row(mainAxisSize: MainAxisSize.min, children: trailing),
    );
  }

  /// Where the approved summary went (Connections): sent, scheduled (Undo stops it) or not sent and why.
  Widget _receipts(DayReview review) {
    final l10n = context.l10n;
    final rows = <Widget>[];
    for (final r in review.outboundReceipts) {
      final where = r.target == 'orb' ? l10n.connectionsOrbTitle : l10n.connectionsWikiTitle;
      final String text;
      Color color = OmiColors.textSecondary;
      if (r.sent) {
        text = l10n.dayReviewSentTo(where, DateFormat.MMMd().add_jm().format(r.sentAt!)) +
            (r.revision > 0 ? ' · ${l10n.dayReviewSentRevision(r.revision)}' : '');
      } else if (r.scheduled) {
        text = l10n.dayReviewSendsAt(where, DateFormat.jm().format(r.scheduledAt!));
      } else if (r.error.isNotEmpty) {
        text = l10n.dayReviewNotSentTo(where, r.error);
        color = OmiColors.warning;
      } else {
        continue;
      }
      rows.add(Padding(
        padding: const EdgeInsets.only(top: 2),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(r.sent ? Icons.cloud_done_outlined : Icons.cloud_upload_outlined, size: 16, color: color),
          const SizedBox(width: OmiSpacing.xxs),
          Expanded(child: Text(text, style: OmiType.caption.copyWith(color: color))),
        ]),
      ));
    }
    if (rows.isEmpty) return const SizedBox.shrink();
    return Padding(
      key: const Key('day_review_receipts'),
      padding: const EdgeInsets.only(top: OmiSpacing.xxs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: rows),
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
            // The tick only means "merge with another"; say so to touch and screen-reader users.
            Tooltip(
              message: l10n.dayReviewMergeTooltip,
              child: Checkbox(
                value: _selected.contains(c.id),
                onChanged: dropped ? null : (_) => _toggleSelected(c.id),
                visualDensity: VisualDensity.compact,
                side: BorderSide(color: OmiColors.textSecondary),
              ),
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
            key: Key('day_review_paragraph_remove_${c.id}'),
            tooltip: context.l10n.delete,
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
        key: Key('day_review_correction_delete_${c.id}'),
        tooltip: l10n.delete,
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
              if (section != null && !review.isDraft)
                IconButton(
                  key: Key('day_review_section_send_${section.id}'),
                  tooltip: l10n.sendToAgentTitle,
                  visualDensity: VisualDensity.compact,
                  icon: Icon(Icons.smart_toy_outlined, size: 20, color: OmiColors.textSecondary),
                  onPressed: () => _sendSection(section),
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
                child: _reviewClip(review, c, color: OmiColors.surface2, keepButton: true),
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

  /// The rest of the day's review: the tasks it produced (edit, complete, add) and the dates Omi
  /// heard (check, fix, add or decline). Without these the day review ends at the summary and the
  /// user has to find both elsewhere.
  Widget _fromThisDay() {
    final l10n = context.l10n;
    final day = DateTime.tryParse(widget.date);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OmiSectionHeader(l10n.dayReviewFromThisDay),
        if (day != null)
          WaitingTasksSection(
            controller: _waiting,
            day: day,
            onChanged: _load,
            showHeader: false,
            padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
          ),
        OmiSettingsGroup(
          children: [
            if (day != null)
              OmiSettingsRow(
                key: const Key('day_review_tasks'),
                leading: const Icon(Icons.check_circle_outline),
                title: l10n.tasks,
                subtitle: l10n.dayReviewTasksRowBody,
                showChevron: true,
                onTap: () => routeToPage(context, DayTasksPage(date: day)),
              ),
            OmiSettingsRow(
              key: const Key('day_review_calendar'),
              leading: const Icon(Icons.event_outlined),
              title: l10n.familyCalendarTitle,
              subtitle: l10n.familyCalendarBannerBody,
              showChevron: true,
              onTap: () => routeToPage(context, const FamilyCalendarReviewPage()),
            ),
          ],
        ),
      ],
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
              if (!review.isDraft) _receipts(review),
              if (paragraphs.isNotEmpty) ...[
                const SizedBox(height: OmiSpacing.xxs),
                Text(l10n.dayReviewEditHint, style: OmiType.caption.copyWith(color: OmiColors.textSecondary)),
                Row(
                  children: [
                    if (review.editedByUser)
                      Text(l10n.dayReviewEditedByYou,
                          key: const Key('day_review_edited_note'),
                          style: OmiType.caption.copyWith(color: OmiColors.textSecondary)),
                    const Spacer(),
                    OmiButton.tertiary(
                      key: const Key('day_review_edit_text'),
                      label: l10n.dayReviewEditText,
                      icon: Icons.edit_outlined,
                      size: OmiButtonSize.compact,
                      // Not awaited: the button would spin for as long as the sheet is open.
                      onPressed: () {
                        _editText();
                      },
                    ),
                  ],
                ),
              ],
              if (review.isDraft) ...[
                const SizedBox(height: OmiSpacing.sm),
                Container(
                  key: const Key('day_review_draft_notice'),
                  padding: const EdgeInsets.all(OmiSpacing.sm),
                  decoration: BoxDecoration(color: OmiColors.surface1, borderRadius: OmiRadius.mdAll),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.hourglass_top_rounded, size: 18, color: OmiColors.textSecondary),
                      const SizedBox(width: OmiSpacing.xs),
                      Expanded(
                        child: Text(l10n.approvalDraftNotice,
                            style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                      ),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: OmiSpacing.md),
              if (kept.isNotEmpty) ...[
                _group(l10n.dayReviewKeptTitle(kept.length), [for (final c in kept) _reviewClip(review, c)],
                    initiallyExpanded: kept.length <= 3),
                const SizedBox(height: OmiSpacing.xs),
              ],
              for (final card in cards) _sectionCard(review, card.$1, card.$2, byId, anchoredAt),
              for (final c in anchoredAt(null)) _userParagraph(c),
              if (paragraphs.isEmpty) _addParagraphButton(null),
              const SizedBox(height: OmiSpacing.md),
              _fromThisDay(),
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
                _group(l10n.dayReviewRolledTitle(rolled.length), [for (final c in rolled) _reviewClip(review, c)]),
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
        if (review.pendingCorrectionCount > 0 || review.isDraft)
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xxs, OmiSpacing.md, OmiSpacing.sm),
              child: Row(
                children: [
                  if (review.pendingCorrectionCount > 0)
                    Expanded(
                      child: review.isDraft
                          ? OmiButton.secondary(
                              key: const Key('day_review_apply'),
                              label: l10n.dayReviewApplyButton(review.pendingCorrectionCount),
                              // Not awaited: a confirmation may open first; the page shows its own progress.
                              onPressed: () {
                                _regenerate();
                              },
                            )
                          : ElevatedButton(
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
                  if (review.pendingCorrectionCount > 0 && review.isDraft) const SizedBox(width: OmiSpacing.sm),
                  if (review.isDraft)
                    Expanded(
                      child: OmiButton(
                        key: const Key('day_review_approve'),
                        label: l10n.approvalApproveDay,
                        icon: Icons.check_rounded,
                        onPressed: () {
                          _approve();
                        },
                      ),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
