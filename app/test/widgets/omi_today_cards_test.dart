import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/today/today_card.dart';
import 'package:omi/pages/today/today_page.dart';
import 'package:omi/services/today/companion_clients.dart';
import 'package:omi/services/today/feed_parser.dart';
import 'package:omi/services/today/news_feed_service.dart';
import 'package:omi/services/today/today_feed_settings.dart';
import 'package:omi/ui/ui.dart';

Widget _host(Widget child) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: SingleChildScrollView(child: child)),
    );

void main() {
  group('TodayStateView', () {
    Widget view(TodayCardState<String>? state, {FutureOr<void> Function()? onRetry}) => _host(
          TodayStateView<String>(state: state, builder: (_, data) => Text(data), onRetry: onRetry),
        );

    testWidgets('null is loading', (tester) async {
      await tester.pumpWidget(view(null));
      expect(find.byType(OmiLoadingState), findsOneWidget);
    });

    testWidgets('empty shows its title, message and action', (tester) async {
      var tapped = false;
      await tester.pumpWidget(view(
          TodayCardEmpty(title: 'Nothing', message: 'Add one', actionLabel: 'Add', onAction: () => tapped = true)));
      expect(find.byType(OmiEmptyState), findsOneWidget);
      expect(find.text('Add one'), findsOneWidget);
      await tester.tap(find.text('Add'));
      expect(tapped, isTrue);
    });

    testWidgets('error offers Try Again', (tester) async {
      var retried = 0;
      await tester.pumpWidget(view(const TodayCardError('Broken'), onRetry: () => retried++));
      expect(find.byType(OmiErrorState), findsOneWidget);
      await tester.tap(find.text('Try Again'));
      expect(retried, 1);
    });

    testWidgets('data uses the builder', (tester) async {
      await tester.pumpWidget(view(const TodayCardData('hello')));
      expect(find.text('hello'), findsOneWidget);
    });
  });

  group('News card', () {
    testWidgets('no feeds: empty state with Add Feeds', (tester) async {
      var opened = false;
      await tester.pumpWidget(_host(TodayNewsCard(
        feeds: const [],
        load: (_) async => throw StateError('must not fetch'),
        onSetUp: () => opened = true,
      )));
      await tester.pumpAndSettle();
      expect(find.text('No News Feeds'), findsOneWidget);
      await tester.tap(find.text('Add Feeds'));
      expect(opened, isTrue);
    });

    testWidgets('loading, then headlines with a note for feeds that failed', (tester) async {
      final completer = Completer<NewsDigest>();
      await tester.pumpWidget(
          _host(TodayNewsCard(feeds: const ['https://a.example/rss'], load: (_) => completer.future, onSetUp: () {})));
      expect(find.byType(OmiLoadingState), findsOneWidget);
      completer.complete(const NewsDigest(
        items: [FeedItem(title: 'Headline one', source: 'Daily Wire')],
        failedFeeds: ['https://b.example/rss'],
      ));
      await tester.pumpAndSettle();
      expect(find.text('Headline one'), findsOneWidget);
      expect(find.text('Daily Wire'), findsOneWidget);
      expect(find.text("Feeds that couldn't be read: 1"), findsOneWidget);
    });

    testWidgets('every feed failing is an error', (tester) async {
      await tester.pumpWidget(_host(TodayNewsCard(
        feeds: const ['https://a.example/rss'],
        load: (_) async => const NewsDigest(items: [], failedFeeds: ['https://a.example/rss']),
        onSetUp: () {},
      )));
      await tester.pumpAndSettle();
      expect(find.text("Couldn't load your feeds."), findsOneWidget);
    });
  });

  group('Companion cards', () {
    testWidgets('Coinpile not set up asks to set it up without fetching', (tester) async {
      await tester.pumpWidget(_host(TodayCoinpileCard(
        endpoint: const CompanionEndpoint(),
        load: () async => throw StateError('must not fetch'),
        onSetUp: () {},
      )));
      await tester.pumpAndSettle();
      expect(find.text('Not Set Up'), findsOneWidget);
      expect(find.text('Set Up'), findsOneWidget);
    });

    testWidgets('Coinpile summary rows', (tester) async {
      await tester.pumpWidget(_host(TodayCoinpileCard(
        endpoint: const CompanionEndpoint(url: 'https://coinpile.example', hasToken: true),
        load: () async => const CompanionData(
            CoinpileSummary(netWorth: 1000, spentThisMonth: 50, nextBillName: 'Rent', alertsTotal: 2)),
        onSetUp: () {},
      )));
      await tester.pumpAndSettle();
      expect(find.text('Net worth'), findsOneWidget);
      expect(find.text('Spent this month'), findsOneWidget);
      expect(find.text('Next bill: Rent'), findsOneWidget);
      expect(find.text('Alerts: 2'), findsOneWidget);
    });

    testWidgets('a refused token is an error that says so', (tester) async {
      await tester.pumpWidget(_host(TodayPonderingCard(
        endpoint: const CompanionEndpoint(url: 'https://orb.example'),
        load: () async => const CompanionFailure('unauthorized', 401),
        onSetUp: () {},
      )));
      await tester.pumpAndSettle();
      expect(find.text('The token was refused. Check it in the Omi Today settings.'), findsOneWidget);
    });

    testWidgets('no brief yet is empty, a brief shows its lines', (tester) async {
      await tester.pumpWidget(_host(TodayPonderingCard(
        endpoint: const CompanionEndpoint(url: 'https://orb.example'),
        load: () async => const CompanionNothing(),
        onSetUp: () {},
      )));
      await tester.pumpAndSettle();
      expect(find.text('No brief yet today'), findsOneWidget);

      await tester.pumpWidget(_host(TodayPonderingCard(
        key: const Key('second'),
        endpoint: const CompanionEndpoint(url: 'https://orb.example'),
        load: () async =>
            const CompanionData(PonderingBrief(date: '2026-11-26', text: '# Good morning\n- Dentist at 14:00')),
        onSetUp: () {},
      )));
      await tester.pumpAndSettle();
      expect(find.text('Good morning'), findsOneWidget);
      expect(find.text('• Dentist at 14:00'), findsOneWidget);
    });
  });
}
