// The day review's sorting and editing contract, against a fake server:
//  * "Bundle the day" keeps what was starred on this page, not only what the server had starred;
//  * a paragraph being typed survives a stray close (Discard changes? first) and Save sends it;
//  * removing a correction is undoable, and Undo sends the same correction back;
//  * a reviewed day links on to its tasks and the calendar check.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/day_review/day_review_page.dart';

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

DayReview _review(List<DayReviewCorrection> corrections) => DayReview(
      date: _date,
      title: 'Swim, a doctor and dinner',
      summaryText: _summary,
      finalText: _summary,
      corrections: corrections,
      keptConversationIds: const [],
      rolledUpConversationIds: const ['c1', 'c2'],
    );

class _FakeApi extends DayReviewApi {
  _FakeApi({this.review});

  DayReview? review;
  final finalized = <({List<String> keep, List<List<String>> groups, List<String> drop})>[];
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

Future<void> _open(WidgetTester tester, _FakeApi api) async {
  tester.view.physicalSize = const Size(390 * 3, 844 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: DayReviewPage(date: _date, api: api),
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
}
