import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;

/// The user's CalDAV settings as the server reports them (never the password).
class CalDavSettings {
  final bool configured;
  final bool bundled;
  final String url;
  final String username;
  final bool hasPassword;
  final String calendarHref;
  final String tasksHref;
  final bool enabled;
  final bool pushTasks;
  final bool pullTasks;
  final bool pushDaySummaries;
  final bool pushConversationEvents;
  final bool pushConversations;
  final bool pullEvents;

  /// 'list' (my tasks are to-dos in my own task list) or 'parent' (subtasks of one task named
  /// after me in a list shared with others on the server).
  final String taskMode;
  final String ownerLabel;
  final String suggestedCalendarName;
  final String suggestedTasksName;
  final String parentTaskName;

  /// The bundled server: every Omi user on this server shares one CalDAV login.
  final bool sharedAccount;
  final DateTime? lastSyncAt;
  final String lastError;
  final Map<String, dynamic> lastReport;

  const CalDavSettings({
    required this.configured,
    this.bundled = false,
    this.url = '',
    this.username = '',
    this.hasPassword = false,
    this.calendarHref = '',
    this.tasksHref = '',
    this.enabled = true,
    this.pushTasks = true,
    this.pullTasks = true,
    this.pushDaySummaries = true,
    this.pushConversationEvents = true,
    this.pushConversations = false,
    this.pullEvents = true,
    this.taskMode = 'list',
    this.ownerLabel = '',
    this.suggestedCalendarName = '',
    this.suggestedTasksName = '',
    this.parentTaskName = '',
    this.sharedAccount = false,
    this.lastSyncAt,
    this.lastError = '',
    this.lastReport = const {},
  });

  bool get parentMode => taskMode == 'parent';

  int reportCount(String key) => (lastReport[key] as num?)?.toInt() ?? 0;

  factory CalDavSettings.fromGenerated(wire.GeneratedCalDavSettingsResponse g) => CalDavSettings(
        configured: g.configured,
        bundled: g.bundled,
        url: g.url,
        username: g.username,
        hasPassword: g.hasPassword,
        calendarHref: g.calendarHref,
        tasksHref: g.tasksHref,
        enabled: g.enabled,
        pushTasks: g.pushTasks,
        pullTasks: g.pullTasks,
        pushDaySummaries: g.pushDaySummaries,
        pushConversationEvents: g.pushConversationEvents,
        pushConversations: g.pushConversations,
        pullEvents: g.pullEvents,
        taskMode: g.taskMode,
        ownerLabel: g.ownerLabel,
        suggestedCalendarName: g.suggestedCalendarName,
        suggestedTasksName: g.suggestedTasksName,
        parentTaskName: g.parentTaskName,
        sharedAccount: g.sharedAccount,
        lastSyncAt: g.lastSyncAt?.toLocal(),
        lastError: g.lastError,
        lastReport: g.lastReport ?? const {},
      );
}

/// A calendar or task list on the CalDAV server, as the picker shows it.
class CalDavCollection {
  final String href;
  final String name;
  final List<String> components;

  /// Other people on this Omi server whose sync uses it.
  final List<String> usedBy;

  /// 'calendar' and/or 'tasks' when it is this user's chosen one.
  final List<String> mine;

  const CalDavCollection({
    required this.href,
    this.name = '',
    this.components = const [],
    this.usedBy = const [],
    this.mine = const [],
  });

  /// A server that lists no component set accepts everything.
  bool supports(String component) => components.isEmpty || components.contains(component);
  bool get isCalendar => supports('VEVENT');
  bool get isTaskList => supports('VTODO');

  factory CalDavCollection.fromGenerated(wire.GeneratedCalDavCollection g) => CalDavCollection(
        href: g.href,
        name: g.name,
        components: g.components ?? const [],
        usedBy: g.usedBy ?? const [],
        mine: g.mine ?? const [],
      );
}

class CalDavSyncOutcome {
  final bool ok;
  final int tasksPushed;
  final int tasksPulled;
  final int tasksImported;
  final int eventsPushed;
  final int eventsPulled;
  final List<String> errors;
  final String error;

  const CalDavSyncOutcome({
    required this.ok,
    this.tasksPushed = 0,
    this.tasksPulled = 0,
    this.tasksImported = 0,
    this.eventsPushed = 0,
    this.eventsPulled = 0,
    this.errors = const [],
    this.error = '',
  });

  factory CalDavSyncOutcome.fromGenerated(wire.GeneratedCalDavSyncResponse g) => CalDavSyncOutcome(
        ok: g.ok,
        tasksPushed: g.tasksPushed,
        tasksPulled: g.tasksPulled,
        tasksImported: g.tasksImported,
        eventsPushed: g.eventsPushed,
        eventsPulled: g.eventsPulled,
        errors: g.errors ?? const [],
        error: g.error,
      );
}
