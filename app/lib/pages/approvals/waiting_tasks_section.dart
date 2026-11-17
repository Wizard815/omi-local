// omi-local: "Waiting for your OK" — tasks Omi heard in conversations. Nothing here reaches the
// user's calendar app, reminders or task apps until it is approved; a decline can be undone.
import 'package:flutter/material.dart';

import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/pages/day_review/day_review_text_sheet.dart';
import 'package:omi/providers/waiting_approval_controller.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

class WaitingTasksSection extends StatelessWidget {
  const WaitingTasksSection({super.key, required this.controller, this.only, this.showEmpty = false});

  final WaitingApprovalController? controller;

  /// Show these tasks only (the day review's "From this day"); null = every waiting task.
  final List<ActionItemWithMetadata> Function(WaitingApprovalController)? only;

  /// Say "nothing waiting" instead of collapsing when there is nothing.
  final bool showEmpty;

  @override
  Widget build(BuildContext context) {
    final state = controller;
    if (state == null) return const SizedBox.shrink();
    return ListenableBuilder(
      listenable: state,
      builder: (context, _) {
        final tasks = only == null ? state.tasks : only!(state);
        final l10n = context.l10n;
        if (tasks.isEmpty && !showEmpty) return const SizedBox.shrink();
        return Padding(
          key: const ValueKey('waiting_tasks'),
          padding: const EdgeInsets.all(OmiSpacing.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              OmiSectionHeader(l10n.approvalWaitingTitle, subtitle: l10n.approvalWaitingSubtitle),
              if (tasks.isEmpty)
                Text(l10n.approvalNothingWaiting, style: OmiType.subhead.copyWith(color: OmiColors.textSecondary)),
              for (final task in tasks) _WaitingTaskRow(controller: state, task: task),
            ],
          ),
        );
      },
    );
  }
}

class _WaitingTaskRow extends StatelessWidget {
  const _WaitingTaskRow({required this.controller, required this.task});

  final WaitingApprovalController controller;
  final ActionItemWithMetadata task;

  Future<void> _approve(BuildContext context, {bool edit = false}) async {
    final l10n = context.l10n;
    String? text;
    if (edit) {
      text = await showDayReviewTextSheet(
        context,
        title: l10n.approvalEditTitle,
        initialText: task.description,
        minLines: 2,
      );
      if (text == null || !context.mounted) return;
    }
    final ok = await controller.approve(task, description: text);
    if (!context.mounted) return;
    ok ? OmiFeedback.confirm(context, l10n.approvalTaskApproved) : OmiFeedback.error(context, l10n.approvalFailed);
  }

  Future<void> _decline(BuildContext context) async {
    final l10n = context.l10n;
    final ok = await controller.decline(task);
    if (!context.mounted) return;
    if (!ok) {
      OmiFeedback.error(context, l10n.approvalFailed);
      return;
    }
    OmiFeedback.undo(context, l10n.approvalTaskDeclined, onUndo: () => controller.undoDecline(task));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final busy = controller.busy(task.id);
    final due = task.dueAt;
    return Padding(
      key: ValueKey('waiting_task_${task.id}'),
      padding: const EdgeInsets.symmetric(vertical: OmiSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(task.description, style: OmiType.callout.copyWith(color: OmiColors.textPrimary)),
          if (due != null)
            Text(l10n.approvalDue(MaterialLocalizations.of(context).formatMediumDate(due.toLocal())),
                style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
          const SizedBox(height: OmiSpacing.xs),
          Wrap(
            spacing: OmiSpacing.sm,
            children: [
              OmiButton.secondary(
                key: ValueKey('approve_task_${task.id}'),
                label: l10n.approvalApprove,
                size: OmiButtonSize.compact,
                isLoading: busy,
                onPressed: busy ? null : () => _approve(context),
              ),
              OmiButton.tertiary(
                key: ValueKey('edit_approve_task_${task.id}'),
                label: l10n.approvalEditApprove,
                size: OmiButtonSize.compact,
                onPressed: busy ? null : () => _approve(context, edit: true),
              ),
              OmiButton.tertiary(
                key: ValueKey('decline_task_${task.id}'),
                label: l10n.approvalDecline,
                size: OmiButtonSize.compact,
                onPressed: busy ? null : () => _decline(context),
              ),
            ],
          ),
          Divider(color: OmiColors.border),
        ],
      ),
    );
  }
}
