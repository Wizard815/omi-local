// "Waiting for your OK": a task Omi heard is approved (as is, or edited first) or declined, and
// a decline can be undone. The banner line counts tasks, events and summary drafts.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/l10n/app_localizations_en.dart';
import 'package:omi/pages/approvals/approvals_page.dart';
import 'package:omi/pages/approvals/waiting_tasks_section.dart';
import 'package:omi/providers/waiting_approval_controller.dart';

import 'fake_approvals.dart';

Future<WaitingApprovalController> _open(WidgetTester tester, FakeApprovals api) async {
  tester.view.physicalSize = const Size(390 * 3, 844 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  final controller = WaitingApprovalController(api: api);
  addTearDown(controller.dispose);
  await controller.load();
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: Scaffold(body: SingleChildScrollView(child: WaitingTasksSection(controller: controller))),
  ));
  await tester.pumpAndSettle();
  return controller;
}

void main() {
  testWidgets('a waiting task is approved with one tap and leaves the list', (tester) async {
    final api = FakeApprovals(tasks: [waitingTask('t1', 'Call the plumber'), waitingTask('t2', 'Order the cake')]);
    await _open(tester, api);
    expect(find.text('Waiting for your OK'), findsOneWidget);
    expect(find.text('Call the plumber'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('approve_task_t1')));
    await tester.pumpAndSettle();
    expect(api.approved.single, (id: 't1', description: null));
    expect(find.text('Call the plumber'), findsNothing);
    expect(find.text('Order the cake'), findsOneWidget);
  });

  testWidgets('edit & approve sends the edited text', (tester) async {
    final api = FakeApprovals(tasks: [waitingTask('t1', 'Call the plumber')]);
    await _open(tester, api);
    await tester.tap(find.byKey(const ValueKey('edit_approve_task_t1')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(find.widgetWithText(TextField, 'Call the plumber'), findsOneWidget, reason: 'prefilled with the task');
    await tester.enterText(find.byKey(const Key('day_review_text_field')), 'Call the plumber about the leak');
    await tester.pump();
    await tester.tap(find.byKey(const Key('day_review_text_save')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(api.approved.single, (id: 't1', description: 'Call the plumber about the leak'));
  });

  testWidgets('a decline is undoable', (tester) async {
    final api = FakeApprovals(tasks: [waitingTask('t1', 'Call the plumber')]);
    await _open(tester, api);
    await tester.tap(find.byKey(const ValueKey('decline_task_t1')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(api.declined.single, (id: 't1', declined: true));
    expect(find.text('Call the plumber'), findsNothing);
    expect(find.text('Declined'), findsOneWidget);

    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    expect(api.declined.last, (id: 't1', declined: false));
    expect(find.text('Call the plumber'), findsOneWidget, reason: 'it waits again');
  });

  test('the banner line names what waits, in plain words', () {
    final l10n = AppLocalizationsEn();
    expect(
      waitingLine(
        l10n,
        WaitingForApproval(
          tasks: [waitingTask('a', 'x'), waitingTask('b', 'y'), waitingTask('c', 'z')],
          eventCount: 2,
          draftDates: const ['2026-11-15'],
        ),
        today: '2026-11-16',
      ),
      "3 tasks, 2 events and yesterday's summary waiting for your OK",
    );
    expect(
        waitingLine(l10n, const WaitingForApproval(eventCount: 1), today: '2026-11-16'), '1 event waiting for your OK');
    expect(waitingLine(l10n, const WaitingForApproval(), today: '2026-11-16'), isNull);
  });
}
