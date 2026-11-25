// Approve all: a tap approves everything waiting (tasks, events, day summary drafts) at once and
// Undo takes exactly that batch back; holding the button (or "Choose…") opens a checklist with
// everything ticked, and "Approve selected" approves only what stays ticked. The day review's
// variant is scoped to its day.
import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/approvals/waiting_tasks_section.dart';
import 'package:omi/providers/waiting_approval_controller.dart';

import 'fake_approvals.dart';

FakeApprovals _waiting() => FakeApprovals(
      tasks: [
        waitingTask('t1', 'Call the plumber', created: DateTime(2026, 11, 15, 9)),
        waitingTask('t2', 'Order the cake', created: DateTime(2026, 11, 14, 9)),
      ],
      events: [heardEvent('c1', 'Dentist', heard: DateTime(2026, 11, 15, 11))],
      draftDates: ['2026-11-15'],
    );

Future<WaitingApprovalController> _open(WidgetTester tester, FakeApprovals api, {DateTime? day}) async {
  tester.view.physicalSize = const Size(390 * 3, 900 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  final controller = WaitingApprovalController(api: api);
  addTearDown(controller.dispose);
  await controller.load();
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: Scaffold(body: SingleChildScrollView(child: WaitingTasksSection(controller: controller, day: day))),
  ));
  await tester.pumpAndSettle();
  return controller;
}

void main() {
  testWidgets('a tap on Approve all approves everything at once, and Undo takes exactly that back', (tester) async {
    final api = _waiting();
    await _open(tester, api);
    expect(find.text('Approve All (4)'), findsOneWidget);
    expect(find.text('Hold to choose what to approve'), findsOneWidget);

    await tester.tap(find.byKey(const Key('approve_all')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(api.batches.single.all, isTrue, reason: 'no confirmation: the tap approves');
    expect(find.text('Approved 2 tasks, 1 event and 1 day summary'), findsOneWidget);
    expect(find.text('Call the plumber'), findsNothing);
    expect(find.byKey(const Key('approve_all')), findsNothing, reason: 'nothing waits any more');

    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    final undone = api.undone.single;
    expect(undone.taskIds, ['t1', 't2']);
    expect(undone.events.single.conversationId, 'c1');
    expect(undone.dayDates, ['2026-11-15']);
    expect(find.text('Call the plumber'), findsOneWidget, reason: 'it waits again');
    expect(find.text('Approve All (4)'), findsOneWidget);
  });

  testWidgets('holding Approve all opens a checklist; unticking the summary leaves it waiting', (tester) async {
    final api = _waiting();
    await _open(tester, api);
    await tester.longPress(find.byKey(const Key('approve_all')));
    await tester.pumpAndSettle();
    expect(api.batches, isEmpty, reason: 'holding only opens the choice');
    expect(find.byKey(const Key('approval_checklist')), findsOneWidget);
    expect(find.text('Approve Selected (4)'), findsOneWidget, reason: 'everything starts ticked');
    expect(find.textContaining('Dentist · Thu, Nov 19'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('approval_pick_day_2026-11-15')));
    await tester.pump();
    expect(find.text('Approve Selected (3)'), findsOneWidget);
    await tester.tap(find.byKey(const Key('approval_pick_approve')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    final chosen = api.batches.single.chosen!;
    expect(chosen.taskIds, ['t1', 't2']);
    expect(chosen.events.single.conversationId, 'c1');
    expect(chosen.dayDates, isEmpty);
    expect(api.draftDates, ['2026-11-15'], reason: 'the summary still waits for its own OK');
    expect(find.text('Approved 2 tasks and 1 event'), findsOneWidget);
    expect(find.byKey(const Key('approval_checklist')), findsNothing);
  });

  testWidgets('"Choose…" opens the same checklist without a long-press, and Cancel approves nothing', (tester) async {
    final api = _waiting();
    await _open(tester, api);
    await tester.tap(find.byKey(const Key('approve_all_choose')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('approval_checklist')), findsOneWidget);
    await tester.tap(find.byKey(const Key('approval_pick_cancel')));
    await tester.pumpAndSettle();
    expect(api.batches, isEmpty);
    expect(find.text('Approve All (4)'), findsOneWidget);
  });

  testWidgets('the long-press is offered to screen readers too', (tester) async {
    final handle = tester.ensureSemantics();
    await _open(tester, _waiting());
    final node = tester.getSemantics(find.byKey(const Key('approve_all_hold')));
    expect(node.getSemanticsData().hasAction(SemanticsAction.longPress), isTrue);
    handle.dispose();
  });

  testWidgets("the day review's variant approves only that day's items", (tester) async {
    final api = _waiting();
    await _open(tester, api, day: DateTime(2026, 11, 15));
    expect(find.text('Approve All from This Day (3)'), findsOneWidget);
    await tester.tap(find.byKey(const Key('approve_all')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(api.batches.single.date, '2026-11-15');
    expect(api.tasks.map((t) => t.id), ['t2'], reason: 'the task of another day still waits');
    expect(api.draftDates, isEmpty, reason: "that day's summary draft was included");
  });
}
