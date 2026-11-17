// The day review's sorting and editing contract, against a fake server:
//  * "Bundle the day" keeps what was starred on this page, not only what the server had starred;
//  * a paragraph being typed survives a stray close (Discard changes? first) and Save sends it;
//  * removing a correction is undoable, and Undo sends the same correction back;
//  * a reviewed day links on to its tasks and the calendar check;
//  * "Edit text" edits the title and each section in an edit sheet (Discard changes? guard), saves
//    only what changed, and Undo puts the previous text back;
//  * a rewrite (Apply corrections, or approving a draft whose day changed) asks before replacing
//    hand edits.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/day_review/day_review_page.dart';

import '../approvals/fake_approvals.dart';

const _date = '2026-11-15';

DayReviewConversation _clip(String id, String title, {bool starred = false}) => DayReviewConversation(
      id: id,
      title: title,
      emoji: '',
      overview: '',
      startedAt: DateTime(2026, 11, 15, 8),
      finishedAt: DateTime(2026, 11, 15, 8, 20),
      starred: starred,
      rolledUp: false,
    );

const _summary = 'Mia had swim at four.\n\nYou booked the pediatrician for Thursday.';

const _sections = [
  DayReviewSection(id: 's1', conversationIds: ['c1'], title: 'Swim', text: 'Mia had swim at four.'),
  DayReviewSection(
      id: 's2', conversationIds: ['c2'], title: 'The doctor', text: 'You booked the pediatrician for Thursday.'),
];

DayReview _review(
  List<DayReviewCorrection> corrections, {
  bool draft = false,
  String title = 'Swim, a doctor and dinner',
  List<DayReviewSection> sections = const [],
  DateTime? edited,
}) =>
    DayReview(
      date: _date,
      isDraft: draft,
      title: title,
      summaryText: sections.isEmpty ? _summary : sections.map((s) => s.text).join('\n\n'),
      finalText: sections.isEmpty ? _summary : sections.map((s) => s.text).join('\n\n'),
      corrections: corrections,
      sections: sections,
      editedByUserAt: edited,
      keptConversationIds: const [],
      rolledUpConversationIds: const ['c1', 'c2'],
    );

class _FakeApi extends DayReviewApi {
  _FakeApi({this.review});

  DayReview? review;
  int approvals = 0;
  final finalized = <({List<String> keep, List<List<String>> groups, List<String> drop})>[];

  /// The next approve without replaceEdits answers 409 (the day changed since the edit).
  bool approveNeedsRewrite = false;
  final approveCalls = <bool>[];
  final regenerateCalls = <bool>[];
  final edits = <({String? title, Map<String, ({String title, String text})>? sections, String? summaryText})>[];

  @override
  Future<DayReviewResult<DayReview>> approve(String date, {bool replaceEdits = false}) async {
    approveCalls.add(replaceEdits);
    if (approveNeedsRewrite && !replaceEdits) {
      return const DayReviewResult.failed('Rewriting replaces the text you edited.', statusCode: 409);
    }
    approvals++;
    review = _review(review!.corrections, sections: review!.sections, title: review!.title);
    return DayReviewResult.ok(review!);
  }

  @override
  Future<DayReviewResult<DayReview>> regenerate(String date, {bool replaceEdits = false}) async {
    regenerateCalls.add(replaceEdits);
    review = _review(const [], sections: review!.sections, title: review!.title);
    return DayReviewResult.ok(review!);
  }

  @override
  Future<DayReviewResult<DayReview>> editText(String date,
      {String? title, Map<String, ({String title, String text})>? sections, String? summaryText}) async {
    edits.add((title: title, sections: sections, summaryText: summaryText));
    final current = review!;
    review = _review(
      current.corrections,
      draft: current.isDraft,
      title: title ?? current.title,
      sections: [
        for (final s in current.sections)
          sections != null && sections.containsKey(s.id)
              ? DayReviewSection(
                  id: s.id,
                  conversationIds: s.conversationIds,
                  title: sections[s.id]!.title,
                  text: sections[s.id]!.text)
              : s
      ],
      edited: DateTime(2026, 11, 16),
    );
    return DayReviewResult.ok(review!);
  }

  final added = <Map<String, Object?>>[];
  final deleted = <String>[];

  @override
  Future<DayReviewResult<DayReviewDay>> getDay(String date) async => DayReviewResult.ok(DayReviewDay(
        date: date,
        review: review,
        conversations: [
          _clip('c1', 'School drop-off', starred: false),
          _clip('c2', 'Call with the pediatrician'),
          _clip('c3', 'Dinner plans'),
        ],
      ));

  @override
  Future<DayReviewResult<DayReview>> finalize(String date, List<String> keep,
      {List<List<String>> mergeGroups = const [], List<String> drop = const []}) async {
    finalized.add((keep: keep, groups: mergeGroups, drop: drop));
    review = _review(const []);
    return DayReviewResult.ok(review!);
  }

  @override
  Future<DayReviewResult<DayReview>> addCorrection(String date,
      {required DayReviewCorrectionType type,
      String? quote,
      int? start,
      int? end,
      String? text,
      int? afterParagraph}) async {
    added.add({'type': type, 'quote': quote, 'start': start, 'end': end, 'text': text, 'after': afterParagraph});
    review = _review([
      ...review!.corrections,
      DayReviewCorrection(
          id: 'n${added.length}',
          type: type,
          quote: quote,
          start: start,
          end: end,
          text: text,
          afterParagraph: afterParagraph),
    ]);
    return DayReviewResult.ok(review!);
  }

  @override
  Future<DayReviewResult<DayReview>> deleteCorrection(String date, String correctionId) async {
    deleted.add(correctionId);
    review = _review(review!.corrections.where((c) => c.id != correctionId).toList());
    return DayReviewResult.ok(review!);
  }
}

Future<void> _open(WidgetTester tester, _FakeApi api, {FakeApprovals? approvals}) async {
  tester.view.physicalSize = const Size(390 * 3, 844 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: DayReviewPage(date: _date, api: api, approvals: approvals ?? FakeApprovals()),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('the merge tick says what it does', (tester) async {
    await _open(tester, _FakeApi());
    expect(find.byTooltip('Select to merge'), findsNWidgets(3));
  });

  testWidgets('Bundle the day keeps a conversation starred on this page', (tester) async {
    final api = _FakeApi();
    await _open(tester, api);

    await tester.tap(find.byTooltip('Keep as a conversation').at(1)); // the pediatrician call
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('day_review_bundle')));
    await tester.pumpAndSettle();
    expect(find.text('Build the day summary?'), findsOneWidget, reason: 'bundling asks first, like Summarize');
    expect(api.finalized, isEmpty);
    await tester.tap(find.text('Summarize'));
    await tester.pumpAndSettle();

    expect(api.finalized.single.keep, ['c2'], reason: 'the star made here is honored');
    expect(api.finalized.single.groups, isEmpty);
    expect(find.text('Swim, a doctor and dinner'), findsOneWidget);
  });

  testWidgets('a typed paragraph is not lost to a stray close, and Save sends it', (tester) async {
    final api = _FakeApi(review: _review(const []));
    await _open(tester, api);

    await tester.tap(find.byTooltip('Add a paragraph after this section').first);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('day_review_text_field')), 'Grandma called about Sunday.');
    await tester.pump();
    await tester.tap(find.byTooltip('Close'));
    await tester.pumpAndSettle();
    expect(find.text('Discard Changes?'), findsOneWidget);
    await tester.tap(find.text('Keep Editing'));
    await tester.pumpAndSettle();
    expect(find.text('Grandma called about Sunday.'), findsOneWidget, reason: 'the draft is still there');

    await tester.tap(find.byKey(const Key('day_review_text_save')));
    await tester.pumpAndSettle();
    expect(api.added.single['text'], 'Grandma called about Sunday.');
    expect(api.added.single['type'], DayReviewCorrectionType.paragraph);
    expect(find.text('Apply corrections (1)'), findsOneWidget);
  });

  testWidgets('removing a correction offers Undo, which sends the same correction again', (tester) async {
    final api = _FakeApi(
      review: _review(const [
        DayReviewCorrection(
            id: 'k1', type: DayReviewCorrectionType.highlight, quote: 'Thursday', start: 55, end: 63, text: 'Friday'),
      ]),
    );
    await _open(tester, api);

    final delete = find.byKey(const Key('day_review_correction_delete_k1'));
    await tester.scrollUntilVisible(delete, 200, scrollable: find.byType(Scrollable).first);
    await tester.tap(delete);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(api.deleted, ['k1']);
    expect(find.text('Correction removed'), findsOneWidget);

    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    expect(api.added.single, {
      'type': DayReviewCorrectionType.highlight,
      'quote': 'Thursday',
      'start': 55,
      'end': 63,
      'text': 'Friday',
      'after': null,
    });
    expect(find.text('Apply corrections (1)'), findsOneWidget);
  });

  testWidgets('a reviewed day links on to its tasks and the calendar check', (tester) async {
    await _open(tester, _FakeApi(review: _review(const [])));
    final tasks = find.byKey(const Key('day_review_tasks'));
    await tester.scrollUntilVisible(tasks, 200, scrollable: find.byType(Scrollable).first);
    expect(tasks, findsOneWidget);
    expect(find.byKey(const Key('day_review_calendar')), findsOneWidget);
    expect(find.text('From this day'), findsOneWidget);
  });

  testWidgets('a draft says nothing is sent yet, and Approve finalizes it', (tester) async {
    final api = _FakeApi(review: _review(const [], draft: true));
    await _open(tester, api);
    expect(find.byKey(const Key('day_review_draft_notice')), findsOneWidget);
    await tester.tap(find.byKey(const Key('day_review_approve')));
    await tester.pumpAndSettle();
    expect(api.approvals, 1);
    expect(find.byKey(const Key('day_review_draft_notice')), findsNothing);
    expect(find.byKey(const Key('day_review_approve')), findsNothing);
  });

  testWidgets("the day's tasks Omi heard wait for approval in From this day", (tester) async {
    final approvals = FakeApprovals(tasks: [
      waitingTask('t1', 'Call the pediatrician back', created: DateTime(2026, 11, 15, 10)),
      waitingTask('t2', 'A task from another day', created: DateTime(2026, 11, 12, 10)),
    ]);
    await _open(tester, _FakeApi(review: _review(const [])), approvals: approvals);
    final approve = find.byKey(const ValueKey('approve_task_t1'));
    await tester.scrollUntilVisible(approve, 200, scrollable: find.byType(Scrollable).first);
    await tester.ensureVisible(approve);
    await tester.pumpAndSettle();
    expect(find.text('A task from another day'), findsNothing);
    await tester.tap(approve);
    await tester.pumpAndSettle();
    expect(approvals.approved.single.id, 't1');
  });

  testWidgets('Edit text edits the title and a section, saves only what changed, and Undo puts it back',
      (tester) async {
    final api = _FakeApi(review: _review(const [], sections: _sections));
    await _open(tester, api);
    await tester.tap(find.byKey(const Key('day_review_edit_text')));
    await tester.pumpAndSettle();
    expect(find.text('Edit text'), findsWidgets);
    expect(find.widgetWithText(TextField, 'Mia had swim at four.'), findsOneWidget, reason: 'prefilled');

    await tester.enterText(find.byKey(const Key('day_review_edit_title')), 'Swim and the doctor');
    await tester.enterText(find.byKey(const Key('day_review_edit_text_s2')), 'Booked Dr. Lee for Thursday at 9.');
    await tester.pump();
    await tester.tap(find.byKey(const Key('day_review_edit_save')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    final saved = api.edits.single;
    expect(saved.title, 'Swim and the doctor');
    expect(saved.sections!.keys, ['s2'], reason: 'the untouched section is not sent');
    expect(saved.sections!['s2'], (title: 'The doctor', text: 'Booked Dr. Lee for Thursday at 9.'));
    expect(find.text('Summary saved'), findsOneWidget);
    expect(find.text('Swim and the doctor'), findsOneWidget);
    expect(find.byKey(const Key('day_review_edited_note')), findsOneWidget);

    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    final undo = api.edits.last;
    expect(undo.title, 'Swim, a doctor and dinner');
    expect(undo.sections, {'s2': (title: 'The doctor', text: 'You booked the pediatrician for Thursday.')});
    expect(find.text('Swim, a doctor and dinner'), findsOneWidget);
  });

  testWidgets('typed text in Edit text survives a stray close: Discard changes? first', (tester) async {
    final api = _FakeApi(review: _review(const [], sections: _sections));
    await _open(tester, api);
    await tester.tap(find.byKey(const Key('day_review_edit_text')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('day_review_edit_text_s1')), 'Mia swam 400 m.');
    await tester.pump();
    await tester.tap(find.byTooltip('Close'));
    await tester.pumpAndSettle();
    expect(find.text('Discard Changes?'), findsOneWidget);
    await tester.tap(find.text('Keep Editing'));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(TextField, 'Mia swam 400 m.'), findsOneWidget, reason: 'nothing lost');
    await tester.tap(find.byKey(const Key('day_review_edit_cancel')));
    await tester.pumpAndSettle();
    expect(api.edits, isEmpty, reason: 'Cancel saves nothing');
  });

  testWidgets('Apply corrections on an edited summary asks before the rewrite replaces the edits', (tester) async {
    const pending = DayReviewCorrection(id: 'p1', type: DayReviewCorrectionType.paragraph, text: 'Dinner at Gran.');
    final api = _FakeApi(review: _review([pending], draft: true, sections: _sections, edited: DateTime(2026, 11, 16)));
    await _open(tester, api);
    await tester.tap(find.byKey(const Key('day_review_apply')).hitTestable().first);
    await tester.pumpAndSettle();
    expect(find.text('Rewrite will replace your edits — continue?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(api.regenerateCalls, isEmpty);

    await tester.tap(find.byKey(const Key('day_review_apply')).hitTestable().first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Rewrite'));
    await tester.pumpAndSettle();
    expect(api.regenerateCalls, [true]);
  });

  testWidgets('approving an edited draft whose day changed asks, then rewrites with consent', (tester) async {
    final api = _FakeApi(review: _review(const [], draft: true, sections: _sections, edited: DateTime(2026, 11, 16)))
      ..approveNeedsRewrite = true;
    await _open(tester, api);
    await tester.tap(find.byKey(const Key('day_review_approve')));
    await tester.pumpAndSettle();
    expect(find.textContaining('New conversations came in since you edited this summary'), findsOneWidget);
    await tester.tap(find.text('Rewrite'));
    await tester.pumpAndSettle();
    expect(api.approveCalls, [false, true]);
    expect(api.approvals, 1);
  });

  testWidgets("From this day's Approve all includes the day's own summary draft", (tester) async {
    final approvals = FakeApprovals(
      tasks: [waitingTask('t1', 'Call the pediatrician back', created: DateTime(2026, 11, 15, 10))],
      draftDates: [_date],
    );
    final api = _FakeApi(review: _review(const [], draft: true, sections: _sections));
    await _open(tester, api, approvals: approvals);
    final all = find.byKey(const Key('approve_all'));
    await tester.scrollUntilVisible(all, 200, scrollable: find.byType(Scrollable).first);
    await tester.ensureVisible(all);
    await tester.pumpAndSettle();
    expect(find.text('Approve all from this day (2)'), findsOneWidget);
    await tester.tap(all);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(approvals.batches.single.date, _date);
    expect(approvals.draftDates, isEmpty);
  });
}
