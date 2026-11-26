import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'package:omi/services/reminders/task_reminder_plan.dart';
import 'package:omi/services/reminders/task_reminder_scheduler.dart';
import 'package:omi/services/reminders/task_reminder_settings.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// Settings, Notifications: task reminders on this phone (on/off and how early), and on Android
/// whether they may fire at the exact minute.
class TaskRemindersSettingsGroup extends StatefulWidget {
  const TaskRemindersSettingsGroup({super.key, TaskReminderSettingsStore? store, AwesomeTaskReminderPlatform? platform})
      : _store = store,
        _platform = platform;

  final TaskReminderSettingsStore? _store;
  final AwesomeTaskReminderPlatform? _platform;

  /// "15 mins before", or "At due time only" for 0.
  static String leadLabel(BuildContext context, int minutes) => minutes == 0
      ? context.l10n.taskReminderAtDueTime
      : context.l10n.taskReminderLeadBefore(OmiDuration.long(minutes * 60, context.l10n));

  @override
  State<TaskRemindersSettingsGroup> createState() => _TaskRemindersSettingsGroupState();
}

class _TaskRemindersSettingsGroupState extends State<TaskRemindersSettingsGroup> with WidgetsBindingObserver {
  late final TaskReminderSettingsStore _store = widget._store ?? TaskReminderSettingsStore.instance;
  late final AwesomeTaskReminderPlatform _platform = widget._platform ?? AwesomeTaskReminderPlatform();
  bool? _allowed;
  bool? _exact;

  @override
  void initState() {
    super.initState();
    _store.addListener(_changed);
    WidgetsBinding.instance.addObserver(this);
    unawaited(_store.ensureLoaded());
    unawaited(_checkPermissions());
  }

  @override
  void dispose() {
    _store.removeListener(_changed);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) unawaited(_checkPermissions());
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  Future<void> _checkPermissions() async {
    bool? allowed, exact;
    try {
      allowed = await _platform.notificationsAllowed();
      exact = await _platform.exactAlarmsAllowed();
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      _allowed = allowed;
      _exact = exact;
    });
  }

  Future<void> _setEnabled(bool enabled) async {
    await _store.setEnabled(enabled);
    if (enabled && _allowed == false) {
      try {
        await _platform.requestNotifications();
      } catch (_) {}
      await _checkPermissions();
    }
  }

  Future<void> _pickLead() async {
    final current = _store.value.leadMinutes;
    final picked = await showOmiSheet<int>(
      context: context,
      title: context.l10n.taskReminderLead,
      builder: (sheetContext) => OmiSettingsGroup(
        children: [
          for (final minutes in TaskReminderSettings.leadChoices)
            OmiSettingsRow(
              key: ValueKey('task_reminder_lead_$minutes'),
              title: TaskRemindersSettingsGroup.leadLabel(sheetContext, minutes),
              trailing: minutes == current ? Icon(Icons.check, color: OmiColors.textPrimary) : null,
              showChevron: false,
              onTap: () => Navigator.of(sheetContext).pop(minutes),
            ),
        ],
      ),
    );
    if (picked != null) await _store.setLeadMinutes(picked);
  }

  Future<void> _requestExact() async {
    try {
      await _platform.requestExactAlarms();
    } catch (_) {}
    await _checkPermissions();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final value = _store.value;
    return OmiSettingsGroup(
      footer: _allowed == false && value.enabled ? l10n.taskReminderNotificationsOff : null,
      children: [
        OmiSettingsRow.toggle(
          key: const Key('task_reminders_toggle'),
          leading: const Icon(Icons.alarm_outlined),
          title: l10n.taskReminders,
          subtitle: l10n.taskRemindersSubtitle,
          value: value.enabled,
          onChanged: (on) => unawaited(_setEnabled(on)),
        ),
        AnimatedOpacity(
          opacity: value.enabled ? 1.0 : 0.4,
          duration: OmiMotion.of(context).standard,
          child: OmiSettingsRow(
            key: const Key('task_reminders_lead'),
            leading: const Icon(Icons.timelapse_outlined),
            title: l10n.taskReminderLead,
            value: TaskRemindersSettingsGroup.leadLabel(context, value.leadMinutes),
            onTap: value.enabled ? _pickLead : null,
          ),
        ),
        if (Platform.isAndroid && value.enabled && _exact != null)
          OmiSettingsRow(
            key: const Key('task_reminders_exact'),
            leading: const Icon(Icons.schedule_outlined),
            title: l10n.taskReminderExactAlarms,
            subtitle: _exact! ? null : l10n.taskReminderExactAlarmsOff,
            value: _exact! ? l10n.on : l10n.off,
            onTap: _exact! ? null : _requestExact,
          ),
      ],
    );
  }
}
