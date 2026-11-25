// omi-local: "Approve all" for what waits for the user's OK (tasks Omi heard, heard events, day
// summary drafts). A tap approves everything waiting at once (on the day review: everything of
// that day) and offers Undo, which takes back exactly that batch. Holding the button, or "Choose…"
// for those who cannot long-press, opens a checklist with every item ticked; the user unticks what
// should keep waiting and taps "Approve selected". Backend: POST /v1/approvals/batch (+ /undo).
import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/providers/waiting_approval_controller.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// "2 tasks, 1 event and 1 day summary".
String approvalBatchItems(AppLocalizations l10n, {int tasks = 0, int events = 0, int days = 0}) {
  final parts = <String>[
    if (tasks > 0) l10n.approvalCountTasks(tasks),
    if (events > 0) l10n.approvalCountEvents(events),
    if (days > 0) l10n.approvalCountSummaries(days),
  ];
  if (parts.isEmpty) return '';
  if (parts.length == 1) return parts.single;
  return l10n.approvalListAnd(parts.sublist(0, parts.length - 1).join(', '), parts.last);
}

/// Tells the user what a batch approved, with Undo; or that nothing was waiting any more.
void showApprovalBatchFeedback(
  BuildContext context,
  WaitingApprovalController controller,
  ApprovalBatch? batch, {
  VoidCallback? onChanged,
}) {
  final l10n = context.l10n;
  if (batch == null) {
    OmiFeedback.error(context, l10n.approvalFailed);
    return;
  }
  if (batch.isEmpty) {
    if (batch.failedCount > 0) {
      OmiFeedback.error(context, l10n.approvalBatchSomeStillWait(batch.failedCount));
    } else {
      OmiFeedback.info(context, l10n.approvalBatchNothing);
    }
    return;
  }
  final items = approvalBatchItems(
    l10n,
    tasks: batch.taskIds.length,
    events: batch.events.length,
    days: batch.dayDates.length,
  );
  final message = batch.failedCount > 0
      ? '${l10n.approvalBatchApproved(items)}. ${l10n.approvalBatchSomeStillWait(batch.failedCount)}'
      : l10n.approvalBatchApproved(items);
  OmiFeedback.undo(context, message, icon: Icons.done_all_rounded, onUndo: () async {
    await controller.undoBatch(batch);
    onChanged?.call();
  });
}

/// The waiting items in scope: everything, or what is of one local [day].
class ApprovalScope {
  const ApprovalScope(this.controller, {this.day});

  final WaitingApprovalController controller;
  final DateTime? day;

  ApprovalBatch get items => ApprovalBatch(
        taskIds: [for (final t in day == null ? controller.tasks : controller.tasksOf(day!)) t.id],
        events: [
          for (final e in day == null ? controller.events : controller.eventsOf(day!))
            (conversationId: e.conversationId, eventIdx: e.eventIdx)
        ],
        dayDates: day == null ? controller.draftDates : controller.draftsOf(day!),
      );
}

/// The Approve all button, with its "hold to choose" hint and the accessible "Choose…".
class ApproveAllBar extends StatelessWidget {
  const ApproveAllBar({
    super.key,
    required this.scope,
    required this.onApproveAll,
    required this.onChoose,
  });

  final ApprovalScope scope;
  final VoidCallback onApproveAll;
  final VoidCallback onChoose;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final count = scope.items.count;
    if (count == 0) return const SizedBox.shrink();
    final busy = scope.controller.batchBusy;
    final label = scope.day == null ? l10n.approvalApproveAll(count) : l10n.approvalApproveAllFromDay(count);
    return Padding(
      padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Semantics(
            onLongPress: busy ? null : onChoose,
            hint: l10n.approvalHoldToChoose,
            child: GestureDetector(
              key: const Key('approve_all_hold'),
              onLongPress: busy ? null : onChoose,
              child: OmiButton(
                key: const Key('approve_all'),
                label: label,
                icon: Icons.done_all_rounded,
                isLoading: busy,
                onPressed: busy ? null : onApproveAll,
              ),
            ),
          ),
          Row(
            children: [
              Expanded(
                child:
                    Text(l10n.approvalHoldToChoose, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
              ),
              OmiButton.tertiary(
                key: const Key('approve_all_choose'),
                label: l10n.approvalChoose,
                size: OmiButtonSize.compact,
                onPressed: busy ? null : onChoose,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// The checklist: every waiting item in scope, all ticked to start with.
class ApprovalChecklist extends StatefulWidget {
  const ApprovalChecklist({super.key, required this.scope, required this.onApprove, required this.onCancel});

  final ApprovalScope scope;
  final ValueChanged<ApprovalBatch> onApprove;
  final VoidCallback onCancel;

  @override
  State<ApprovalChecklist> createState() => _ApprovalChecklistState();
}

class _ApprovalChecklistState extends State<ApprovalChecklist> {
  late final ApprovalBatch _all = widget.scope.items;
  late final Set<String> _tasks = {..._all.taskIds};
  late final Set<String> _events = {for (final e in _all.events) _eventKey(e)};
  late final Set<String> _days = {..._all.dayDates};

  static String _eventKey(ApprovalEventRef e) => '${e.conversationId}#${e.eventIdx}';

  ApprovalBatch get _chosen => ApprovalBatch(
        taskIds: [
          for (final id in _all.taskIds)
            if (_tasks.contains(id)) id
        ],
        events: [
          for (final e in _all.events)
            if (_events.contains(_eventKey(e))) e
        ],
        dayDates: [
          for (final d in _all.dayDates)
            if (_days.contains(d)) d
        ],
      );

  void _toggle(Set<String> set, String key, bool on) => setState(() => on ? set.add(key) : set.remove(key));

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final controller = widget.scope.controller;
    final tasks = {for (final t in controller.tasks) t.id: t};
    final events = {for (final e in controller.events) e.key.replaceFirst(':', '#'): e};
    final chosen = _chosen;
    final dates = OmiDateFormat.of(context);
    return Column(
      key: const Key('approval_checklist'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: OmiSpacing.xs),
          child: Text(l10n.approvalSelectHint, style: OmiType.subhead.copyWith(color: OmiColors.textSecondary)),
        ),
        for (final id in _all.taskIds)
          KeyedSubtree(
            key: ValueKey('approval_pick_task_$id'),
            child: OmiCheckboxRow(
              label: tasks[id]?.description ?? id,
              value: _tasks.contains(id),
              onChanged: (on) => _toggle(_tasks, id, on),
            ),
          ),
        for (final ref in _all.events)
          KeyedSubtree(
            key: ValueKey('approval_pick_event_${_eventKey(ref)}'),
            child: OmiCheckboxRow(
              label: () {
                final event = events[_eventKey(ref)];
                return event == null
                    ? _eventKey(ref)
                    : '${event.title} · ${dates.weekdayDate(event.start.toLocal())} ${dates.time(event.start.toLocal())}';
              }(),
              value: _events.contains(_eventKey(ref)),
              onChanged: (on) => _toggle(_events, _eventKey(ref), on),
            ),
          ),
        for (final date in _all.dayDates)
          KeyedSubtree(
            key: ValueKey('approval_pick_day_$date'),
            child: OmiCheckboxRow(
              label: l10n.approvalSummaryItem(
                  DateTime.tryParse(date) == null ? date : dates.weekdayDate(DateTime.parse(date))),
              value: _days.contains(date),
              onChanged: (on) => _toggle(_days, date, on),
            ),
          ),
        const SizedBox(height: OmiSpacing.sm),
        // Stacked on a narrow screen or at large text, where "Approve Selected (6)" was cut short.
        OmiButtonPair(
          secondIsPrimary: true,
          secondFlex: 2,
          firstMinWidth: 90,
          secondMinWidth: 200,
          first: OmiButton.secondary(
            key: const Key('approval_pick_cancel'),
            label: l10n.cancel,
            onPressed: widget.onCancel,
          ),
          second: OmiButton(
            key: const Key('approval_pick_approve'),
            label: l10n.approvalApproveSelected(chosen.count),
            isLoading: controller.batchBusy,
            onPressed: chosen.isEmpty || controller.batchBusy ? null : () => widget.onApprove(chosen),
          ),
        ),
      ],
    );
  }
}

/// Approve all for a scope, with the checklist in place while choosing. [taskRows] shows the
/// usual per-task rows when not choosing. [onChanged] runs after a batch or its Undo (the day
/// review reloads: its own draft may have been approved).
class ApproveAllPanel extends StatefulWidget {
  const ApproveAllPanel({super.key, required this.scope, this.taskRows, this.onChanged});

  final ApprovalScope scope;
  final Widget? taskRows;
  final VoidCallback? onChanged;

  @override
  State<ApproveAllPanel> createState() => _ApproveAllPanelState();
}

class _ApproveAllPanelState extends State<ApproveAllPanel> {
  bool _choosing = false;

  Future<void> _run(Future<ApprovalBatch?> Function() call) async {
    final batch = await call();
    if (!mounted) return;
    setState(() => _choosing = false);
    widget.onChanged?.call();
    showApprovalBatchFeedback(context, widget.scope.controller, batch, onChanged: widget.onChanged);
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.scope.controller;
    if (_choosing) {
      return ApprovalChecklist(
        scope: widget.scope,
        onCancel: () => setState(() => _choosing = false),
        onApprove: (chosen) => _run(() => controller.approveSelected(chosen)),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ApproveAllBar(
          scope: widget.scope,
          onApproveAll: () => _run(() => controller.approveAll(day: widget.scope.day)),
          onChoose: () => setState(() => _choosing = true),
        ),
        if (widget.taskRows != null) widget.taskRows!,
      ],
    );
  }
}
