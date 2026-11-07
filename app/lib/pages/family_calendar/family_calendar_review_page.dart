import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/family_calendar.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// omi-local: the events Omi heard, each shown with its date and time for the user to confirm,
/// fix or decline before it goes to the family calendar (dayGLANCE through ALLGlance).
/// Nothing on this page is written anywhere until "Add" is tapped.
class FamilyCalendarReviewPage extends StatefulWidget {
  const FamilyCalendarReviewPage({super.key, this.load, this.approve, this.decline});

  /// Seams for tests; the app uses the /v1/family-calendar client.
  final Future<ApiResult<FamilyCalendar>> Function()? load;
  final Future<ApiResult<FamilyCalendarOutcome>> Function(FamilyCalendarEvent event,
      {String? title, String? description, DateTime? start, int? duration})? approve;
  final Future<ApiResult<FamilyCalendarOutcome>> Function(FamilyCalendarEvent event)? decline;

  @override
  State<FamilyCalendarReviewPage> createState() => _FamilyCalendarReviewPageState();
}

class _FamilyCalendarReviewPageState extends State<FamilyCalendarReviewPage> {
  FamilyCalendar? _calendar;
  bool _loading = true;
  String? _loadError;
  final Set<String> _busy = {};
  final Map<String, _Draft> _drafts = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final result = await (widget.load ?? getFamilyCalendar)();
    if (!mounted) return;
    setState(() {
      _loading = false;
      switch (result) {
        case ApiSuccess(:final data):
          _calendar = data;
          _loadError = null;
        case ApiFailure(:final problem):
          _loadError = problem.kind.name;
      }
    });
  }

  _Draft _draft(FamilyCalendarEvent event) => _drafts.putIfAbsent(event.key, () => _Draft.of(event));

  Future<void> _approve(FamilyCalendarEvent event) async {
    final draft = _draft(event);
    setState(() => _busy.add(event.key));
    final result = await (widget.approve ?? approveFamilyCalendarEvent)(
      event,
      title: draft.title.text.trim(),
      start: draft.start,
      duration: draft.duration,
    );
    if (!mounted) return;
    setState(() => _busy.remove(event.key));
    switch (result) {
      case ApiSuccess(:final data) when data.ok:
        OmiFeedback.confirm(context, context.l10n.familyCalendarAdded(draft.title.text.trim()));
        _drafts.remove(event.key);
        await _load();
      case ApiSuccess(:final data):
        OmiFeedback.error(context, context.l10n.familyCalendarFailed(data.error));
      case ApiFailure(:final problem):
        OmiFeedback.error(context, context.l10n.familyCalendarFailed(problem.kind.name));
    }
  }

  Future<void> _decline(FamilyCalendarEvent event) async {
    setState(() => _busy.add(event.key));
    final result = await (widget.decline ?? declineFamilyCalendarEvent)(event);
    if (!mounted) return;
    setState(() => _busy.remove(event.key));
    switch (result) {
      case ApiSuccess(:final data) when data.ok:
        _drafts.remove(event.key);
        await _load();
      case ApiSuccess(:final data):
        OmiFeedback.error(context, context.l10n.familyCalendarFailed(data.error));
      case ApiFailure(:final problem):
        OmiFeedback.error(context, context.l10n.familyCalendarFailed(problem.kind.name));
    }
  }

  Future<void> _pickDate(FamilyCalendarEvent event) async {
    final draft = _draft(event);
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: draft.start,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 3),
    );
    if (picked == null || !mounted) return;
    setState(() => draft.start = DateTime(picked.year, picked.month, picked.day, draft.start.hour, draft.start.minute));
  }

  Future<void> _pickTime(FamilyCalendarEvent event) async {
    final draft = _draft(event);
    final picked = await showTimePicker(context: context, initialTime: TimeOfDay.fromDateTime(draft.start));
    if (picked == null || !mounted) return;
    setState(
        () => draft.start = DateTime(draft.start.year, draft.start.month, draft.start.day, picked.hour, picked.minute));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final calendar = _calendar;
    final Widget body;
    if (_loading) {
      body = const OmiLoadingState();
    } else if (calendar == null) {
      body = OmiErrorState(message: l10n.familyCalendarFailed(_loadError ?? ''), onRetry: _load);
    } else if (!calendar.configured) {
      body = OmiEmptyState(
        icon: Icons.family_restroom_outlined,
        title: l10n.familyCalendarNotConfiguredTitle,
        message: l10n.familyCalendarNotConfiguredBody,
      );
    } else {
      body = RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xs, OmiSpacing.md, OmiSpacing.xxl),
          children: [
            Text(l10n.familyCalendarIntro(calendar.label),
                style: OmiType.footnote.copyWith(color: OmiColors.textSecondary, height: 1.4)),
            const SizedBox(height: OmiSpacing.md),
            if (calendar.pending.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: OmiSpacing.xl),
                child: OmiEmptyState(
                  icon: Icons.event_available_outlined,
                  title: l10n.familyCalendarNothingPending,
                  message: l10n.familyCalendarNothingPendingBody,
                ),
              ),
            for (final event in calendar.pending) _pendingCard(event),
            if (calendar.recent.isNotEmpty) ...[
              const SizedBox(height: OmiSpacing.md),
              OmiSectionHeader(l10n.familyCalendarRecentTitle),
              for (final event in calendar.recent) _recentRow(event),
            ],
          ],
        ),
      );
    }
    return Scaffold(
      backgroundColor: OmiColors.surface0,
      appBar: AppBar(
        backgroundColor: OmiColors.surface0,
        leading: const OmiBackButton(),
        title: Text(l10n.familyCalendarTitle),
      ),
      body: body,
    );
  }

  Widget _pendingCard(FamilyCalendarEvent event) {
    final l10n = context.l10n;
    final dates = OmiDateFormat.of(context);
    final draft = _draft(event);
    final busy = _busy.contains(event.key);
    final edited = draft.isEdited(event);
    return Container(
      key: Key('family_calendar_event_${event.key}'),
      margin: const EdgeInsets.only(bottom: OmiSpacing.sm),
      padding: const EdgeInsets.all(OmiSpacing.md),
      decoration: BoxDecoration(color: OmiColors.surface1, borderRadius: BorderRadius.circular(OmiRadius.lg)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.hearing_outlined, size: 14, color: OmiColors.textTertiary),
              const SizedBox(width: OmiSpacing.xxs),
              Expanded(
                child: Text(
                  event.heardAt == null
                      ? l10n.familyCalendarHeardIn(event.conversationTitle)
                      : l10n.familyCalendarHeardInAt(event.conversationTitle, dates.timestamp(event.heardAt!)),
                  style: OmiType.caption.copyWith(color: OmiColors.textTertiary),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const SizedBox(height: OmiSpacing.sm),
          TextField(
            key: Key('family_calendar_title_${event.key}'),
            controller: draft.title,
            enabled: !busy,
            style: OmiType.headline,
            decoration: InputDecoration(
              isDense: true,
              border: InputBorder.none,
              contentPadding: EdgeInsets.zero,
              hintText: l10n.familyCalendarTitleHint,
            ),
            onChanged: (_) => setState(() {}),
          ),
          if (event.description.isNotEmpty) ...[
            const SizedBox(height: OmiSpacing.xxs),
            Text(event.description,
                style: OmiType.footnote.copyWith(color: OmiColors.textSecondary),
                maxLines: 3,
                overflow: TextOverflow.ellipsis),
          ],
          const SizedBox(height: OmiSpacing.sm),
          Wrap(
            spacing: OmiSpacing.xs,
            runSpacing: OmiSpacing.xs,
            children: [
              _chip(
                key: Key('family_calendar_date_${event.key}'),
                icon: Icons.calendar_today_outlined,
                label: dates.dayHeader(draft.start),
                onTap: busy ? null : () => _pickDate(event),
              ),
              _chip(
                key: Key('family_calendar_time_${event.key}'),
                icon: Icons.access_time,
                label: dates.timeRange(draft.start, draft.start.add(Duration(minutes: draft.duration))),
                onTap: busy ? null : () => _pickTime(event),
              ),
              _chip(
                key: Key('family_calendar_duration_${event.key}'),
                icon: Icons.timelapse_outlined,
                label: l10n.familyCalendarMinutes(draft.duration),
                onTap: busy ? null : () => setState(() => draft.duration = _nextDuration(draft.duration)),
              ),
            ],
          ),
          if (edited) ...[
            const SizedBox(height: OmiSpacing.xs),
            Text(l10n.familyCalendarEditedNote, style: OmiType.caption.copyWith(color: OmiColors.warning)),
          ],
          const SizedBox(height: OmiSpacing.sm),
          Row(
            children: [
              Expanded(
                child: OmiButton.secondary(
                  key: Key('family_calendar_decline_${event.key}'),
                  label: l10n.familyCalendarDecline,
                  size: OmiButtonSize.compact,
                  onPressed: busy ? null : () => _decline(event),
                ),
              ),
              const SizedBox(width: OmiSpacing.sm),
              Expanded(
                child: OmiButton(
                  key: Key('family_calendar_approve_${event.key}'),
                  label: l10n.familyCalendarApprove,
                  size: OmiButtonSize.compact,
                  isLoading: busy,
                  onPressed: busy || draft.title.text.trim().isEmpty ? null : () => _approve(event),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _recentRow(FamilyCalendarEvent event) {
    final l10n = context.l10n;
    final dates = OmiDateFormat.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: OmiSpacing.xs),
      child: Row(
        children: [
          Icon(event.created ? Icons.event_available_outlined : Icons.event_busy_outlined,
              size: 18, color: event.created ? OmiColors.success : OmiColors.textTertiary),
          const SizedBox(width: OmiSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(event.title, style: OmiType.subhead, maxLines: 1, overflow: TextOverflow.ellipsis),
                Text(
                  '${dates.dayHeader(event.start)}, ${dates.time(event.start)} · ${event.created ? l10n.familyCalendarOnCalendar : l10n.familyCalendarDeclined}',
                  style: OmiType.caption.copyWith(color: OmiColors.textTertiary),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _chip({required Key key, required IconData icon, required String label, VoidCallback? onTap}) {
    return Material(
      key: key,
      color: OmiColors.surface2,
      borderRadius: BorderRadius.circular(OmiRadius.pill),
      child: InkWell(
        borderRadius: BorderRadius.circular(OmiRadius.pill),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.sm, vertical: OmiSpacing.xs),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 14, color: OmiColors.textSecondary),
              const SizedBox(width: OmiSpacing.xxs),
              Text(label, style: OmiType.footnote.copyWith(color: OmiColors.textPrimary)),
              const SizedBox(width: OmiSpacing.xxs),
              Icon(Icons.edit_outlined, size: 12, color: OmiColors.textTertiary),
            ],
          ),
        ),
      ),
    );
  }
}

/// Tapping the length cycles through the usual meeting lengths.
int _nextDuration(int current) {
  const steps = [15, 30, 45, 60, 90, 120, 180];
  for (final step in steps) {
    if (step > current) return step;
  }
  return steps.first;
}

/// The user's corrections to one heard event, kept while the page is open.
class _Draft {
  final TextEditingController title;
  DateTime start;
  int duration;

  _Draft(this.title, this.start, this.duration);

  factory _Draft.of(FamilyCalendarEvent event) =>
      _Draft(TextEditingController(text: event.title), event.start, event.duration);

  bool isEdited(FamilyCalendarEvent event) =>
      title.text.trim() != event.title || start != event.start || duration != event.duration;
}
