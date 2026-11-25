// The Day reviews list: a failed read says so and offers Try Again (it never looks like "no
// reviews yet"), and a loaded list shows the days to sort and the reviewed days.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/day_review/day_reviews_list_page.dart';
import 'package:omi/ui/ui.dart';

typedef _Pending = ({String today, List<String> pendingDates, List<String> draftDates});

DayReview _review(String date, String title) => DayReview(
      date: date,
      title: title,
      summaryText: 'A day.',
      finalText: 'A day.',
      corrections: const [],
      keptConversationIds: const [],
      rolledUpConversationIds: const [],
    );

Future<void> _pump(WidgetTester tester, DayReviewsListPage page) async {
  await tester.pumpWidget(MaterialApp(
    theme: buildOmiTheme(),
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: page,
  ));
  await tester.pumpAndSettle();
}

AppLocalizations _l10n(WidgetTester tester) => AppLocalizations.of(tester.element(find.byType(Scaffold).first));

void main() {
  testWidgets('a failed read shows the error with Try Again, which reads again', (tester) async {
    var reads = 0;
    await _pump(
      tester,
      DayReviewsListPage(
        loadPending: ({int days = 7}) async {
          reads++;
          return reads == 1 ? null : (today: '2026-11-25', pendingDates: ['2026-11-24'], draftDates: <String>[]);
        },
        loadReviews: ({int limit = 30}) async => const <DayReview>[],
      ),
    );
    expect(find.byType(OmiErrorState), findsOneWidget);
    expect(find.text(_l10n(tester).dayReviewsLoadFailed), findsOneWidget);
    expect(find.text(_l10n(tester).dayReviewsNone), findsNothing);

    await tester.tap(find.text(_l10n(tester).tryAgain));
    await tester.pumpAndSettle();
    expect(reads, 2);
    expect(find.byType(OmiErrorState), findsNothing);
    expect(find.byKey(const ValueKey('day_reviews_pending_2026-11-24')), findsOneWidget);
  });

  testWidgets('nothing to sort and nothing reviewed is an empty state', (tester) async {
    await _pump(
      tester,
      DayReviewsListPage(
        loadPending: ({int days = 7}) async =>
            (today: '2026-11-25', pendingDates: <String>[], draftDates: <String>[]) as _Pending?,
        loadReviews: ({int limit = 30}) async => const <DayReview>[],
      ),
    );
    expect(find.byType(OmiEmptyState), findsOneWidget);
  });

  testWidgets('days to sort and reviewed days; an untitled review is named by its day', (tester) async {
    await _pump(
      tester,
      DayReviewsListPage(
        loadPending: ({int days = 7}) async =>
            (today: '2026-11-25', pendingDates: ['2026-11-23', '2026-11-24'], draftDates: <String>[]) as _Pending?,
        loadReviews: ({int limit = 30}) async => [_review('2026-11-20', 'Shelves planned'), _review('2026-11-19', ' ')],
      ),
    );
    expect(find.byKey(const ValueKey('day_reviews_pending_2026-11-24')), findsOneWidget);
    expect(find.text('Shelves planned'), findsOneWidget);
    expect(find.byKey(const ValueKey('day_reviews_done_2026-11-19')), findsOneWidget);
    expect(find.text(_l10n(tester).dayReviewUntitled), findsNothing);
  });
}
