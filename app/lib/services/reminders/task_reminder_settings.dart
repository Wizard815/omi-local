import 'package:flutter/foundation.dart';

import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/services/reminders/task_reminder_plan.dart';

/// Settings, Notifications, Task reminders: on/off and the lead time, kept on this phone (the
/// reminders are this phone's own notifications).
class TaskReminderSettingsStore extends ChangeNotifier {
  TaskReminderSettingsStore._();

  static final TaskReminderSettingsStore instance = TaskReminderSettingsStore._();

  @visibleForTesting
  factory TaskReminderSettingsStore.forTest() => TaskReminderSettingsStore._();

  static const enabledKey = 'task_reminders_enabled';
  static const leadKey = 'task_reminders_lead_minutes';

  TaskReminderSettings _value = const TaskReminderSettings();
  bool _loaded = false;

  TaskReminderSettings get value => _value;
  bool get loaded => _loaded;

  Future<void> ensureLoaded() async {
    if (_loaded) return;
    final prefs = await SharedPreferences.getInstance();
    final lead = prefs.getInt(leadKey);
    _value = TaskReminderSettings(
      enabled: prefs.getBool(enabledKey) ?? true,
      leadMinutes: lead != null && TaskReminderSettings.leadChoices.contains(lead)
          ? lead
          : TaskReminderSettings.defaultLeadMinutes,
    );
    _loaded = true;
    notifyListeners();
  }

  Future<void> setEnabled(bool enabled) => _set(_value.copyWith(enabled: enabled));

  Future<void> setLeadMinutes(int minutes) {
    if (!TaskReminderSettings.leadChoices.contains(minutes)) return Future.value();
    return _set(_value.copyWith(leadMinutes: minutes));
  }

  Future<void> _set(TaskReminderSettings next) async {
    if (next == _value && _loaded) return;
    _value = next;
    _loaded = true;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(enabledKey, next.enabled);
    await prefs.setInt(leadKey, next.leadMinutes);
  }
}
