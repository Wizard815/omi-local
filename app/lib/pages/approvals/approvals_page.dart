// omi-local: everything that waits for the user's OK in one place — tasks Omi heard, events in the
// Calendar check and day summary drafts. Opened from the day review banner. Nothing listed here has
// reached the user's calendar app, reminders or task apps yet.
import 'package:flutter/material.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api/connections.dart';
import 'package:omi/backend/schema/connections.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/approvals/waiting_tasks_section.dart';
import 'package:omi/pages/connections/agent_jobs_page.dart';
import 'package:omi/pages/day_review/day_review_page.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';
import 'package:omi/pages/settings/caldav_settings_page.dart';
import 'package:omi/providers/waiting_approval_controller.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// "3 tasks, 2 events and yesterday's summary are waiting for your OK", or null when nothing waits.
String? waitingLine(AppLocalizations l10n, WaitingForApproval waiting, {required String today}) {
  final parts = <String>[
    if (waiting.taskCount > 0) l10n.approvalCountTasks(waiting.taskCount),
    if (waiting.eventCount > 0) l10n.approvalCountEvents(waiting.eventCount),
    ...waiting.draftDates.map((d) => _summaryLabel(l10n, d, today)),
  ];
  if (parts.isEmpty) return null;
  final list = parts.length == 1
      ? parts.single
      : l10n.approvalListAnd(parts.sublist(0, parts.length - 1).join(', '), parts.last);
  return l10n.approvalWaitingLine(list);
}

String _summaryLabel(AppLocalizations l10n, String date, String today) {
  final day = DateTime.tryParse(date);
  final now = DateTime.tryParse(today);
  if (day != null && now != null && now.difference(day).inDays == 1) return l10n.approvalYesterdaysSummary;
  return l10n.approvalDaySummary(day == null ? date : DateFormat.MMMEd().format(day));
}

class ApprovalsPage extends StatefulWidget {
  const ApprovalsPage({super.key, this.api = const ApprovalsApi(), this.connections = const ConnectionsApi()});

  final ApprovalsApi api;

  /// omi-local: agent jobs whose agent asks before it goes on (Hermes is answered here).
  final ConnectionsApi connections;

  @override
  State<ApprovalsPage> createState() => _ApprovalsPageState();
}

class _ApprovalsPageState extends State<ApprovalsPage> {
  late final WaitingApprovalController _controller = WaitingApprovalController(api: widget.api);

  /// Agent jobs waiting for an answer (never part of Approve all, never auto-approved).
  List<AgentJob> _asking = const [];

  @override
  void initState() {
    super.initState();
    _controller.load();
    _loadAsking();
  }

  Future<void> _loadAsking() async {
    final ConnectionResult<List<AgentJob>> result;
    try {
      result = await widget.connections.listAgentJobs();
    } catch (_) {
      return; // The agents' questions are an extra section; tasks and drafts stand without it.
    }
    if (!mounted || result.value == null) return;
    setState(() => _asking = result.value!.where((j) => j.waiting).toList());
  }

  Future<void> _refresh() async {
    await Future.wait([_controller.load(), _loadAsking()]);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: OmiColors.surface0,
      appBar: AppBar(backgroundColor: OmiColors.surface0, title: Text(l10n.approvalPageTitle)),
      body: ListenableBuilder(
        listenable: _controller,
        builder: (context, _) {
          final state = _controller.state;
          if (!_controller.loaded && !_controller.failed) return const Center(child: OmiSpinner());
          if (_controller.failed && !_controller.loaded) {
            return OmiErrorState(message: l10n.somethingWentWrong, onRetry: _controller.load);
          }
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView(
              padding: const EdgeInsets.only(bottom: OmiSpacing.xl),
              children: [
                if (_asking.isNotEmpty)
                  Padding(
                    key: const Key('approval_agents_asking'),
                    padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.sm, OmiSpacing.md, 0),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(l10n.approvalAgentsAsking, style: OmiType.headline.copyWith(color: OmiColors.textPrimary)),
                      const SizedBox(height: OmiSpacing.xs),
                      for (final job in _asking)
                        AgentJobCard(job: job, api: widget.connections, onChanged: _loadAsking),
                    ]),
                  ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.sm, OmiSpacing.md, 0),
                  child: Text(
                    state.autoApprove ? l10n.approvalAutoOnFooter(state.autoApproveTime) : l10n.approvalAutoOffFooter,
                    style: OmiType.subhead.copyWith(color: OmiColors.textSecondary),
                  ),
                ),
                WaitingTasksSection(controller: _controller, showEmpty: true, showHeader: false),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.md),
                  child: OmiSettingsGroup(
                    children: [
                      for (final date in state.draftDates)
                        OmiSettingsRow(
                          key: ValueKey('approval_draft_$date'),
                          leading: const Icon(Icons.auto_stories_outlined),
                          title: _summaryLabel(l10n, date, state.today),
                          subtitle: l10n.approvalDraftRowBody,
                          onTap: () async {
                            await routeToPage(context, DayReviewPage(date: date));
                            _controller.load();
                          },
                        ),
                      OmiSettingsRow(
                        key: const Key('approval_events_row'),
                        leading: const Icon(Icons.event_outlined),
                        title: l10n.familyCalendarTitle,
                        value: state.eventCount == 0 ? null : l10n.approvalCountEvents(state.eventCount),
                        onTap: () async {
                          await routeToPage(context, const FamilyCalendarReviewPage());
                          _controller.load();
                        },
                      ),
                      OmiSettingsRow(
                        key: const Key('approval_settings_row'),
                        leading: const Icon(Icons.schedule_outlined),
                        title: l10n.approvalAutoTitle,
                        value: state.autoApprove ? state.autoApproveTime : l10n.approvalOff,
                        onTap: () async {
                          await routeToPage(context, const CalDavSettingsPage());
                          _controller.load();
                        },
                      ),
                    ],
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
