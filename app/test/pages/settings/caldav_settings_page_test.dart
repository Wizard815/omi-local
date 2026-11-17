// Settings → Calendar sync on a shared CalDAV account: the user's own calendar and task list
// (picked from the server's, others' marked, or created with their name), the task mode, Sync
// now, and auto-approve (off unless turned on).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/caldav.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/settings/caldav_settings_page.dart';

import '../approvals/fake_approvals.dart';

const _home = 'http://127.0.0.1:5232/omi/';

class _FakeCalDav extends CalDavSettingsApi {
  CalDavSettings settings = const CalDavSettings(
    configured: true,
    bundled: true,
    sharedAccount: true,
    url: 'http://127.0.0.1:5232/',
    username: 'omi',
    hasPassword: true,
    calendarHref: '${_home}omi-alice/',
    tasksHref: '${_home}omi-tasks-alice/',
    ownerLabel: 'Alice',
    suggestedCalendarName: 'Omi – Alice',
    suggestedTasksName: 'Omi Tasks – Alice',
    parentTaskName: "Alice's tasks",
  );
  final updates = <Map<String, Object?>>[];
  final created = <(String, String)>[];
  int syncs = 0;

  @override
  Future<ApiResult<CalDavSettings>> load() async => ApiSuccess(settings);

  @override
  Future<ApiResult<({bool ok, List<CalDavCollection> collections, String error})>> collections() async =>
      const ApiSuccess((
        ok: true,
        collections: [
          CalDavCollection(href: '${_home}omi-alice/', name: 'Omi – Alice', components: ['VEVENT'], mine: ['calendar']),
          CalDavCollection(href: '${_home}omi-bob/', name: 'Omi – Bob', components: ['VEVENT'], usedBy: ['Bob']),
          CalDavCollection(href: '${_home}omi-tasks-alice/', name: 'Omi Tasks – Alice', components: ['VTODO']),
          CalDavCollection(href: '${_home}family/', name: 'Family', components: ['VTODO'], usedBy: ['Bob']),
        ],
        error: '',
      ));

  @override
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
  }) async {
    updates.add({'calendar_href': calendarHref, 'tasks_href': tasksHref, 'task_mode': taskMode});
    settings = CalDavSettings(
      configured: true,
      bundled: true,
      sharedAccount: true,
      url: settings.url,
      username: settings.username,
      calendarHref: calendarHref ?? settings.calendarHref,
      tasksHref: tasksHref ?? settings.tasksHref,
      taskMode: taskMode ?? settings.taskMode,
      ownerLabel: settings.ownerLabel,
      suggestedCalendarName: settings.suggestedCalendarName,
      suggestedTasksName: settings.suggestedTasksName,
      parentTaskName: settings.parentTaskName,
    );
    return ApiSuccess(settings);
  }

  @override
  Future<ApiResult<({bool ok, CalDavSettings? settings, String error})>> create(String name, String kind) async {
    created.add((name, kind));
    return ApiSuccess((ok: true, settings: settings, error: ''));
  }

  @override
  Future<ApiResult<CalDavSyncOutcome>> sync() async {
    syncs++;
    return const ApiSuccess(CalDavSyncOutcome(ok: true, tasksPushed: 2, eventsPushed: 1));
  }
}

Future<void> _open(WidgetTester tester, _FakeCalDav api, FakeApprovals approvals) async {
  tester.view.physicalSize = const Size(390 * 3, 1400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: CalDavSettingsPage(api: api, approvals: approvals),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows the shared account and the user\'s own calendar and task list', (tester) async {
    await _open(tester, _FakeCalDav(), FakeApprovals());
    expect(find.text('Server: http://127.0.0.1:5232/ · Account: omi'), findsOneWidget);
    expect(find.text('This account is shared by everyone on this Omi server.'), findsOneWidget);
    expect(find.text('Omi – Alice'), findsOneWidget);
    expect(find.text('Omi Tasks – Alice'), findsOneWidget);
    expect(find.text('My own task list'), findsOneWidget);
  });

  testWidgets('the picker marks collections other people use, and picking one saves it', (tester) async {
    final api = _FakeCalDav();
    await _open(tester, api, FakeApprovals());
    await tester.tap(find.byKey(const Key('caldav_my_tasks')));
    await tester.pumpAndSettle();
    expect(find.text('Used by Bob'), findsOneWidget, reason: 'Family is Bob\'s list too');
    expect(find.text('Omi – Bob'), findsNothing, reason: 'a calendar is not offered as a task list');
    await tester.tap(find.text('Family'));
    await tester.pumpAndSettle();
    expect(api.updates.single['tasks_href'], '${_home}family/');
  });

  testWidgets('Create new… is prefilled with the user\'s name', (tester) async {
    final api = _FakeCalDav();
    await _open(tester, api, FakeApprovals());
    await tester.tap(find.byKey(const Key('caldav_my_calendar')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('caldav_collection_create')));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(TextField, 'Omi – Alice'), findsOneWidget);
    await tester.tap(find.byKey(const Key('day_review_text_save')));
    await tester.pumpAndSettle();
    expect(api.created.single, ('Omi – Alice', 'calendar'));
  });

  testWidgets('the parent-task mode can be chosen', (tester) async {
    final api = _FakeCalDav();
    await _open(tester, api, FakeApprovals());
    await tester.tap(find.byKey(const Key('caldav_task_mode')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('caldav_mode_parent')));
    await tester.pumpAndSettle();
    expect(api.updates.single['task_mode'], 'parent');
    expect(find.text('Under "Alice\'s tasks"'), findsOneWidget);
  });

  testWidgets('auto-approve is off by default; turning it on shows its time', (tester) async {
    final approvals = FakeApprovals();
    await _open(tester, _FakeCalDav(), approvals);
    expect(find.textContaining('Auto-approve is off'), findsOneWidget);
    expect(find.byKey(const Key('approval_auto_time')), findsNothing);
    await tester.tap(find.byKey(const Key('approval_auto_toggle')));
    await tester.pumpAndSettle();
    expect(approvals.settingsValue.autoApprove, isTrue);
    expect(find.byKey(const Key('approval_auto_time')), findsOneWidget);
    expect(find.text('21:00'), findsOneWidget);
  });

  testWidgets('Sync now runs the sync', (tester) async {
    final api = _FakeCalDav();
    await _open(tester, api, FakeApprovals());
    await tester.tap(find.byKey(const Key('caldav_sync_now')));
    await tester.pumpAndSettle();
    expect(api.syncs, 1);
  });
}
