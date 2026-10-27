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
  final DateTime? lastSyncAt;
  final String lastError;

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
    this.lastSyncAt,
    this.lastError = '',
  });

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
        lastSyncAt: g.lastSyncAt?.toLocal(),
        lastError: g.lastError,
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
