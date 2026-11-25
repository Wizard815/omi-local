import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api/caldav.dart' as caldav;
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/caldav.dart';
import 'package:omi/pages/day_review/day_review_text_sheet.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// The server calls this page makes; tests pass a fake.
class CalDavSettingsApi {
  const CalDavSettingsApi();

  Future<ApiResult<CalDavSettings>> load() => caldav.getCalDavSettings();
  Future<ApiResult<CalDavSettings>> update({
    String? url,
    String? username,
    String? password,
    String? calendarHref,
    String? tasksHref,
    String? taskMode,
    bool? enabled,
    bool? pushTasks,
    bool? pullTasks,
    bool? pushDaySummaries,
    bool? pushConversationEvents,
    bool? pushConversations,
    bool? pullEvents,
  }) =>
      caldav.updateCalDavSettings(
        url: url,
        username: username,
        password: password,
        calendarHref: calendarHref,
        tasksHref: tasksHref,
        taskMode: taskMode,
        enabled: enabled,
        pushTasks: pushTasks,
        pullTasks: pullTasks,
        pushDaySummaries: pushDaySummaries,
        pushConversationEvents: pushConversationEvents,
        pushConversations: pushConversations,
        pullEvents: pullEvents,
      );
  Future<ApiResult<CalDavSettings>> forget() => caldav.deleteCalDavSettings();
  Future<ApiResult<({bool ok, int calendars, String error})>> test() => caldav.testCalDavConnection();
  Future<ApiResult<CalDavSyncOutcome>> sync() => caldav.syncCalDavNow();
  Future<ApiResult<({bool ok, List<CalDavCollection> collections, String error})>> collections() =>
      caldav.listCalDavCollections();
  Future<ApiResult<({bool ok, CalDavSettings? settings, String error})>> create(String name, String kind) =>
      caldav.createCalDavCollection(name: name, kind: kind);
}

/// omi-local: Settings → Calendar sync. Where this user's tasks, events and day summaries go on the
/// CalDAV server (the stack's bundled one is shared by everyone on this Omi server, so each person
/// picks a calendar and a task list of their own), and whether anything is approved without them.
class CalDavSettingsPage extends StatefulWidget {
  const CalDavSettingsPage({super.key, this.api = const CalDavSettingsApi(), this.approvals = const ApprovalsApi()});

  final CalDavSettingsApi api;
  final ApprovalsApi approvals;

  @override
  State<CalDavSettingsPage> createState() => _CalDavSettingsPageState();
}

class _CalDavSettingsPageState extends State<CalDavSettingsPage> {
  CalDavSettings? _settings;
  ApprovalSettings? _approval;
  List<CalDavCollection>? _collections;
  bool _loading = true;
  bool _busy = false;
  final _url = TextEditingController();
  final _username = TextEditingController();
  final _password = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _url.dispose();
    _username.dispose();
    _password.dispose();
    super.dispose();
  }

  /// The settings behind a result, or null after showing the failure (the page keeps what it had).
  CalDavSettings? _unwrap(ApiResult<CalDavSettings> result) {
    switch (result) {
      case ApiSuccess(:final data):
        return data;
      case ApiFailure(:final problem):
        OmiFeedback.error(context, context.l10n.caldavFailed(problem.kind.name));
        return null;
    }
  }

  Future<void> _load() async {
    final result = await widget.api.load();
    final approval = await widget.approvals.settings();
    if (!mounted) return;
    final settings = _unwrap(result);
    setState(() {
      if (settings != null) _settings = settings;
      if (approval case ApiSuccess(:final data)) _approval = data;
      _loading = false;
      if (settings != null && !settings.bundled) {
        _url.text = settings.url;
        _username.text = settings.username;
      }
    });
    if (settings != null && settings.configured) await _loadCollections();
  }

  Future<void> _loadCollections() async {
    final result = await widget.api.collections();
    if (!mounted) return;
    if (result case ApiSuccess(:final data) when data.ok) setState(() => _collections = data.collections);
  }

  Future<void> _run(Future<void> Function() work) async {
    setState(() => _busy = true);
    try {
      await work();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _save() => _run(() async {
        final password = _password.text;
        final result = await widget.api.update(
          url: _url.text.trim(),
          username: _username.text.trim(),
          password: password.isEmpty ? null : password,
        );
        if (!mounted) return;
        final saved = _unwrap(result);
        if (saved == null) return;
        _password.clear();
        setState(() => _settings = saved);
        OmiFeedback.confirm(context, context.l10n.caldavSaved);
        await _loadCollections();
      });

  Future<void> _toggle(String field, bool value) => _run(() async {
        final result = await widget.api.update(
          enabled: field == 'enabled' ? value : null,
          pushTasks: field == 'push_tasks' ? value : null,
          pullTasks: field == 'pull_tasks' ? value : null,
          pushDaySummaries: field == 'push_day_summaries' ? value : null,
          pushConversationEvents: field == 'push_conversation_events' ? value : null,
          pushConversations: field == 'push_conversations' ? value : null,
          pullEvents: field == 'pull_events' ? value : null,
        );
        if (!mounted) return;
        final saved = _unwrap(result);
        if (saved != null) setState(() => _settings = saved);
      });

  Future<void> _test() => _run(() async {
        final result = await widget.api.test();
        if (!mounted) return;
        switch (result) {
          case ApiSuccess(:final data) when data.ok:
            OmiFeedback.confirm(context, context.l10n.caldavCalendarsFound(data.calendars));
          case ApiSuccess(:final data):
            OmiFeedback.error(context, context.l10n.caldavFailed(data.error));
          case ApiFailure(:final problem):
            OmiFeedback.error(context, context.l10n.caldavFailed(problem.kind.name));
        }
      });

  Future<void> _sync() => _run(() async {
        final result = await widget.api.sync();
        if (!mounted) return;
        switch (result) {
          case ApiSuccess(:final data) when data.ok || data.error.isEmpty:
            OmiFeedback.confirm(
                context,
                context.l10n.caldavSyncResult(
                    data.tasksPushed, data.eventsPushed, data.tasksPulled + data.tasksImported + data.eventsPulled));
          case ApiSuccess(:final data):
            OmiFeedback.error(context, context.l10n.caldavFailed(data.error));
          case ApiFailure(:final problem):
            OmiFeedback.error(context, context.l10n.caldavFailed(problem.kind.name));
        }
        await _load();
      });

  Future<void> _forget() => _run(() async {
        final result = await widget.api.forget();
        if (!mounted) return;
        final settings = _unwrap(result);
        if (settings == null) return;
        _url.clear();
        _username.clear();
        _password.clear();
        setState(() => _settings = settings);
      });

  // ---- my calendar / my task list -------------------------------------------------------------

  String _nameOf(String href, {required String fallback}) {
    if (href.isEmpty) return fallback;
    for (final c in _collections ?? const <CalDavCollection>[]) {
      if (c.href == href) return c.name.isEmpty ? href : c.name;
    }
    final parts = href.split('/').where((p) => p.isNotEmpty).toList();
    return parts.isEmpty ? href : Uri.decodeComponent(parts.last);
  }

  /// [kind] 'calendar' or 'tasks'.
  Future<void> _pick(String kind) async {
    final settings = _settings;
    if (settings == null) return;
    if (_collections == null) await _loadCollections();
    if (!mounted) return;
    final l10n = context.l10n;
    final calendar = kind == 'calendar';
    final current = calendar ? settings.calendarHref : settings.tasksHref;
    final options =
        (_collections ?? const <CalDavCollection>[]).where((c) => calendar ? c.isCalendar : c.isTaskList).toList();
    final picked = await showOmiSheet<String>(
      context: context,
      title: calendar ? l10n.caldavMyCalendar : l10n.caldavMyTaskList,
      builder: (sheetContext) => CalDavCollectionPicker(
        options: options,
        current: current,
        onCreate: () => Navigator.of(sheetContext).pop(_createSentinel),
      ),
    );
    if (picked == null || !mounted) return;
    if (picked == _createSentinel) return _create(kind);
    if (picked == current) return;
    await _run(() async {
      final result = await widget.api.update(
        calendarHref: calendar ? picked : null,
        tasksHref: calendar ? null : picked,
      );
      if (!mounted) return;
      final saved = _unwrap(result);
      if (saved == null) return;
      setState(() => _settings = saved);
      OmiFeedback.confirm(context, l10n.caldavCollectionChosen);
      await _loadCollections();
    });
  }

  static const _createSentinel = '\u0000create';

  Future<void> _create(String kind) async {
    final settings = _settings;
    if (settings == null) return;
    final l10n = context.l10n;
    final calendar = kind == 'calendar';
    final name = await showDayReviewTextSheet(
      context,
      title: calendar ? l10n.caldavNewCalendar : l10n.caldavNewTaskList,
      hint: l10n.caldavCollectionNameHint,
      initialText: calendar ? settings.suggestedCalendarName : settings.suggestedTasksName,
      minLines: 1,
    );
    if (name == null || name.trim().isEmpty || !mounted) return;
    await _run(() async {
      final result = await widget.api.create(name.trim(), kind);
      if (!mounted) return;
      switch (result) {
        case ApiSuccess(:final data) when data.ok && data.settings != null:
          setState(() => _settings = data.settings);
          OmiFeedback.confirm(context, l10n.caldavCollectionCreated(name.trim()));
          await _loadCollections();
        case ApiSuccess(:final data):
          OmiFeedback.error(context, l10n.caldavFailed(data.error));
        case ApiFailure(:final problem):
          OmiFeedback.error(context, l10n.caldavFailed(problem.kind.name));
      }
    });
  }

  Future<void> _pickMode() async {
    final settings = _settings;
    if (settings == null) return;
    final l10n = context.l10n;
    final mode = await showOmiSheet<String>(
      context: context,
      title: l10n.caldavTaskModeTitle,
      builder: (sheetContext) => OmiSettingsGroup(
        footer: l10n.caldavTaskModeFooter,
        children: [
          OmiSettingsRow(
            key: const Key('caldav_mode_list'),
            title: l10n.caldavTaskModeList,
            subtitle: l10n.caldavTaskModeListBody,
            trailing: settings.parentMode ? null : const Icon(Icons.check_rounded),
            showChevron: false,
            onTap: () => Navigator.of(sheetContext).pop('list'),
          ),
          OmiSettingsRow(
            key: const Key('caldav_mode_parent'),
            title: l10n.caldavTaskModeParent,
            subtitle: l10n.caldavTaskModeParentBody(_parentName(settings)),
            trailing: settings.parentMode ? const Icon(Icons.check_rounded) : null,
            showChevron: false,
            onTap: () => Navigator.of(sheetContext).pop('parent'),
          ),
        ],
      ),
    );
    if (mode == null || mode == settings.taskMode || !mounted) return;
    await _run(() async {
      final saved = _unwrap(await widget.api.update(taskMode: mode));
      if (saved != null && mounted) setState(() => _settings = saved);
    });
  }

  /// The parent to-do's name on the server ("<name>'s tasks"), as the backend writes it.
  String _parentName(CalDavSettings settings) => settings.parentTaskName;

  // ---- approval -----------------------------------------------------------------------------------

  Future<void> _setAutoApprove(bool on) => _run(() async {
        final result = await widget.approvals.updateSettings(autoApprove: on);
        if (!mounted) return;
        switch (result) {
          case ApiSuccess(:final data):
            setState(() => _approval = data);
          case ApiFailure(:final problem):
            OmiFeedback.error(context, context.l10n.caldavFailed(problem.kind.name));
        }
      });

  Future<void> _pickTime() async {
    final current = _approval?.time ?? '21:00';
    final parts = current.split(':');
    final initial = TimeOfDay(hour: int.tryParse(parts.first) ?? 21, minute: int.tryParse(parts.last) ?? 0);
    final picked = await showTimePicker(context: context, initialTime: initial);
    if (picked == null || !mounted) return;
    final value = '${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}';
    await _run(() async {
      final result = await widget.approvals.updateSettings(time: value);
      if (!mounted) return;
      if (result case ApiSuccess(:final data)) setState(() => _approval = data);
    });
  }

  // ---- build --------------------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final settings = _settings;
    final configured = settings != null && settings.configured;
    return Scaffold(
      backgroundColor: OmiColors.surface0,
      appBar: AppBar(
          backgroundColor: OmiColors.surface0, leading: const OmiBackButton(), title: Text(l10n.caldavSettingsTitle)),
      body: _loading
          ? const Center(child: OmiSpinner())
          : ListView(
              key: const Key('caldav_settings_list'),
              padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xs, OmiSpacing.md, OmiSpacing.xl),
              children: [
                Text(l10n.caldavSettingsIntro, style: OmiType.subhead.copyWith(color: OmiColors.textSecondary)),
                const SizedBox(height: OmiSpacing.sm),
                _status(settings),
                if (configured) ...[
                  const SizedBox(height: OmiSpacing.md),
                  _mine(settings),
                ],
                const SizedBox(height: OmiSpacing.md),
                _approvalGroup(),
                if (configured) ...[
                  const SizedBox(height: OmiSpacing.md),
                  OmiButtonPair(
                    first: OmiButton(
                      key: const Key('caldav_sync_now'),
                      label: l10n.caldavSyncNow,
                      icon: Icons.sync_rounded,
                      onPressed: _busy ? null : _sync,
                    ),
                    second: OmiButton.secondary(
                      key: const Key('caldav_test'),
                      label: l10n.caldavTestConnection,
                      onPressed: _busy ? null : _test,
                    ),
                  ),
                  const SizedBox(height: OmiSpacing.md),
                  OmiSettingsGroup(
                    header: l10n.caldavWhatToSync,
                    children: [
                      _switch('enabled', l10n.caldavEnabled, settings.enabled),
                      _switch('push_tasks', l10n.caldavPushTasks, settings.pushTasks),
                      _switch('pull_tasks', l10n.caldavPullTasks, settings.pullTasks),
                      _switch('push_day_summaries', l10n.caldavPushDaySummaries, settings.pushDaySummaries),
                      _switch('push_conversation_events', l10n.caldavPushConversationEvents,
                          settings.pushConversationEvents),
                      _switch('push_conversations', l10n.caldavPushConversations, settings.pushConversations),
                      _switch('pull_events', l10n.caldavPullEvents, settings.pullEvents),
                    ],
                  ),
                ],
                const SizedBox(height: OmiSpacing.md),
                _ownServer(settings),
              ],
            ),
    );
  }

  Widget _status(CalDavSettings? settings) {
    final l10n = context.l10n;
    final lines = <(String, bool)>[];
    if (settings == null || !settings.configured) {
      lines.add((l10n.caldavNotConfigured, false));
    } else {
      lines.add((l10n.caldavServerAccount(settings.url, settings.username), false));
      if (settings.sharedAccount) lines.add((l10n.caldavSharedAccount, false));
    }
    if (settings?.lastSyncAt != null) {
      lines.add((l10n.caldavLastSync(OmiDateFormat.of(context).dateTime(settings!.lastSyncAt!.toLocal())), false));
      if (settings.lastReport.isNotEmpty) {
        lines.add((
          l10n.caldavLastReport(
            settings.reportCount('tasks_pushed'),
            settings.reportCount('events_pushed'),
            settings.reportCount('tasks_pulled') +
                settings.reportCount('tasks_imported') +
                settings.reportCount('events_pulled'),
          ),
          false
        ));
      }
    }
    if (settings != null && settings.lastError.isNotEmpty) lines.add((l10n.caldavLastError(settings.lastError), true));
    return Container(
      key: const Key('caldav_status'),
      padding: const EdgeInsets.all(OmiSpacing.sm),
      decoration: BoxDecoration(color: OmiColors.surface1, borderRadius: OmiRadius.mdAll),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final (line, error) in lines)
            Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Text(line,
                  style: OmiType.footnote.copyWith(color: error ? OmiColors.danger : OmiColors.textSecondary)),
            ),
        ],
      ),
    );
  }

  Widget _mine(CalDavSettings settings) {
    final l10n = context.l10n;
    return OmiSettingsGroup(
      header: l10n.caldavMineHeader,
      headerSubtitle: settings.sharedAccount ? l10n.caldavMineSharedHint : null,
      children: [
        OmiSettingsRow(
          key: const Key('caldav_my_calendar'),
          leading: const Icon(Icons.calendar_month_outlined),
          title: l10n.caldavMyCalendar,
          value: _nameOf(settings.calendarHref, fallback: settings.suggestedCalendarName),
          onTap: _busy ? null : () => _pick('calendar'),
        ),
        OmiSettingsRow(
          key: const Key('caldav_my_tasks'),
          leading: const Icon(Icons.checklist_rounded),
          title: l10n.caldavMyTaskList,
          value: _nameOf(settings.tasksHref, fallback: settings.suggestedTasksName),
          onTap: _busy ? null : () => _pick('tasks'),
        ),
        OmiSettingsRow(
          key: const Key('caldav_task_mode'),
          leading: const Icon(Icons.account_tree_outlined),
          title: l10n.caldavTaskModeTitle,
          value: settings.parentMode ? l10n.caldavTaskModeParentShort(_parentName(settings)) : l10n.caldavTaskModeList,
          onTap: _busy ? null : _pickMode,
        ),
      ],
    );
  }

  Widget _approvalGroup() {
    final l10n = context.l10n;
    final approval = _approval ?? const ApprovalSettings();
    return OmiSettingsGroup(
      header: l10n.approvalHeader,
      footer: approval.operatorDisabled
          ? l10n.approvalOperatorDisabled
          : approval.autoApprove
              ? l10n.approvalAutoOnFooter(approval.time)
              : l10n.approvalAutoOffFooter,
      children: [
        OmiSettingsRow.toggle(
          key: const Key('approval_auto_toggle'),
          title: l10n.approvalAutoTitle,
          subtitle: l10n.approvalAutoSubtitle,
          value: approval.autoApprove,
          onChanged: _busy || approval.operatorDisabled ? null : _setAutoApprove,
        ),
        if (approval.autoApprove)
          OmiSettingsRow(
            key: const Key('approval_auto_time'),
            title: l10n.approvalAutoTime,
            value: approval.time,
            onTap: _busy ? null : _pickTime,
          ),
      ],
    );
  }

  Widget _ownServer(CalDavSettings? settings) {
    final l10n = context.l10n;
    final own = settings != null && settings.configured && !settings.bundled;
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        key: const Key('caldav_own_server'),
        tilePadding: EdgeInsets.zero,
        initiallyExpanded: settings == null || !settings.configured,
        title: Text(l10n.caldavOwnServer, style: OmiType.headline.copyWith(color: OmiColors.textPrimary)),
        subtitle: Text(own ? settings.url : l10n.caldavOwnServerHint,
            style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
        children: [
          _field(_url, l10n.caldavServerUrl, hint: 'https://cloud.example.org/remote.php/dav/'),
          _field(_username, l10n.caldavUsername),
          _field(_password, l10n.caldavPassword,
              obscure: true, helper: own && settings.hasPassword ? l10n.caldavPasswordKept : null),
          Row(
            children: [
              Expanded(
                child: OmiButton(
                  key: const Key('caldav_save'),
                  label: l10n.caldavSave,
                  onPressed: _busy ? null : _save,
                ),
              ),
              if (own) ...[
                const SizedBox(width: OmiSpacing.sm),
                Expanded(
                  child: OmiButton.tertiary(label: l10n.caldavForget, onPressed: _busy ? null : _forget),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _field(TextEditingController controller, String label, {String? hint, bool obscure = false, String? helper}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
      child: TextField(
        controller: controller,
        obscureText: obscure,
        autocorrect: false,
        style: OmiType.body.copyWith(color: OmiColors.textPrimary),
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          helperText: helper,
          labelStyle: TextStyle(color: OmiColors.textSecondary),
          hintStyle: TextStyle(color: OmiColors.textTertiary),
          helperStyle: TextStyle(color: OmiColors.textTertiary),
          filled: true,
          fillColor: OmiColors.surface1,
          border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
        ),
      ),
    );
  }

  Widget _switch(String field, String label, bool value) => OmiSettingsRow.toggle(
        key: Key('caldav_switch_$field'),
        title: label,
        value: value,
        onChanged: _busy ? null : (v) => _toggle(field, v),
      );
}

/// The list of calendars (or task lists) on the server: the current one ticked, the ones other
/// people on this Omi server use marked, and "Create new…" at the end. Pops the chosen href.
class CalDavCollectionPicker extends StatelessWidget {
  const CalDavCollectionPicker({super.key, required this.options, required this.current, required this.onCreate});

  final List<CalDavCollection> options;
  final String current;
  final VoidCallback onCreate;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return SingleChildScrollView(
      child: Padding(
        padding: const EdgeInsets.only(bottom: OmiSpacing.md),
        child: OmiSettingsGroup(
          footer: l10n.caldavPickerFooter,
          children: [
            for (final c in options)
              OmiSettingsRow(
                key: Key('caldav_collection_${c.href}'),
                title: c.name.isEmpty ? c.href : c.name,
                subtitle: c.usedBy.isEmpty ? null : l10n.caldavUsedBy(c.usedBy.join(', ')),
                trailing: c.href == current ? const Icon(Icons.check_rounded) : null,
                showChevron: false,
                onTap: () => Navigator.of(context).pop(c.href),
              ),
            OmiSettingsRow(
              key: const Key('caldav_collection_create'),
              leading: const Icon(Icons.add_rounded),
              title: l10n.caldavCreateNew,
              onTap: onCreate,
            ),
          ],
        ),
      ),
    );
  }
}
