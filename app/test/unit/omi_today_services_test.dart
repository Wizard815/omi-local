import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/backend/schema/schema.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/services/android_home_widgets.dart';
import 'package:omi/services/today/companion_clients.dart';
import 'package:omi/services/today/feed_parser.dart';
import 'package:omi/services/today/news_feed_service.dart';
import 'package:omi/services/today/today_feed_settings.dart';
import 'package:omi/services/today/today_models.dart';
import 'package:omi/ui/format/omi_date_format.dart';

const rss = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Daily Wire</title>
<item><title>First &amp; foremost</title><link>https://example.com/1</link><pubDate>Wed, 25 Nov 2026 08:30:00 GMT</pubDate></item>
<item><title><![CDATA[<b>Second</b> story]]></title><guid>https://example.com/2</guid><pubDate>Thu, 26 Nov 2026 07:00:00 -0500</pubDate></item>
<item><description>no title</description></item>
</channel></rss>''';

const atom = '''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Blog</title>
<entry><title>Atom entry</title><link rel="self" href="https://example.com/self"/><link href="https://example.com/a"/><updated>2026-11-26T10:00:00Z</updated></entry>
</feed>''';

const rdf = '''<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>RDF News</title></channel>
<item><title>RDF item</title><link>https://example.com/r</link><dc:date>2026-11-24T12:00:00+01:00</dc:date></item>
</rdf:RDF>''';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => initializeDateFormatting());

  group('FeedParser', () {
    test('RSS 2.0: titles, links (guid fallback), dates with zones, untitled items skipped', () {
      final feed = FeedParser.parse(rss);
      expect(feed.title, 'Daily Wire');
      expect(feed.items.map((i) => i.title), ['First & foremost', 'Second story']);
      expect(feed.items[1].link, 'https://example.com/2');
      expect(feed.items[0].published, DateTime.utc(2026, 11, 25, 8, 30));
      expect(feed.items[1].published, DateTime.utc(2026, 11, 26, 12));
      expect(feed.items.every((i) => i.source == 'Daily Wire'), isTrue);
    });

    test('Atom: the alternate link wins over rel=self', () {
      final feed = FeedParser.parse(atom);
      expect(feed.title, 'Atom Blog');
      expect(feed.items.single.link, 'https://example.com/a');
      expect(feed.items.single.published, DateTime.utc(2026, 11, 26, 10));
    });

    test('RSS 1.0 (RDF) with Dublin Core dates', () {
      final feed = FeedParser.parse(rdf);
      expect(feed.title, 'RDF News');
      expect(feed.items.single.published, DateTime.utc(2026, 11, 24, 11));
    });

    test('not a feed throws FeedFormatException', () {
      expect(() => FeedParser.parse('<html><body/></html>'), throwsA(isA<FeedFormatException>()));
      expect(() => FeedParser.parse('not xml at all <'), throwsA(isA<FeedFormatException>()));
    });

    test('dates: RFC 822 without seconds, named zones, garbage', () {
      expect(FeedParser.parseDate('Tue, 3 Nov 2026 09:30 PST'), DateTime.utc(2026, 11, 3, 17, 30));
      expect(FeedParser.parseDate('yesterday'), isNull);
      expect(FeedParser.parseDate(null), isNull);
    });
  });

  group('NewsFeedService', () {
    setUp(() => SharedPreferences.setMockInitialValues({}));

    test('merges feeds newest first, dedupes titles, reports failed feeds, and caches', () async {
      final client = MockClient((request) async {
        if (request.url.host == 'down.example') return http.Response('nope', 500);
        if (request.url.path.endsWith('atom')) return http.Response(atom, 200);
        return http.Response.bytes(utf8.encode(rss), 200);
      });
      final service = NewsFeedService(client: client, now: () => DateTime(2026, 11, 26, 12));
      final digest = await service.load([
        'https://news.example/rss',
        'https://news.example/atom',
        'https://down.example/rss',
        'https://mirror.example/rss',
      ]);
      expect(digest.items.map((i) => i.title), ['Second story', 'Atom entry', 'First & foremost']);
      expect(digest.failedFeeds, ['https://down.example/rss']);
      final cached = await service.cached();
      expect(cached!.items, hasLength(3));
    });

    test('a fresh cache is used without fetching', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response(rss, 200);
      });
      final service = NewsFeedService(client: client, now: () => DateTime(2026, 11, 26, 12));
      await service.load(['https://news.example/rss']);
      await service.load(['https://news.example/rss']);
      expect(calls, 1);
      await service.load(['https://news.example/rss'], force: true);
      expect(calls, 2);
    });
  });

  group('TodayFeedSettings', () {
    test('a saved order from an older build gains the missing cards; junk is dropped', () {
      final settings = TodayFeedSettings.fromJson({
        'order': ['news', 'bogus', 'agenda', 'news'],
        'hidden': ['people'],
        'feeds': ['https://a.example/rss', 'ftp://nope', 'https://a.example/rss'],
        'coinpile_url': 'https://coinpile.example',
      });
      expect(settings.order.take(2), [TodayCardId.news, TodayCardId.agenda]);
      expect(settings.order.toSet(), TodayCardId.values.toSet());
      expect(settings.visible, isNot(contains(TodayCardId.people)));
      expect(settings.feeds, ['https://a.example/rss']);
      expect(settings.coinpile.configured, isTrue);
      expect(settings.pondering.configured, isFalse);
    });

    test('base URLs lose trailing slashes; non-http addresses are refused', () {
      expect(TodayFeedSettings.cleanBaseUrl('https://orb.example:8443/ '), 'https://orb.example:8443');
      expect(TodayFeedSettings.cleanBaseUrl(''), '');
      expect(TodayFeedSettings.cleanBaseUrl('javascript:alert(1)'), isNull);
      expect(TodayFeedSettings.cleanFeedUrl('example.com/rss'), isNull);
    });

    test('the store saves order, visibility, feeds and tokens', () async {
      SharedPreferences.setMockInitialValues({});
      final secrets = MemoryTodaySecretStore();
      final store = TodayFeedSettingsStore(secrets: secrets);
      await store.ensureLoaded();
      await store.reorder(0, 2);
      expect(store.value.order.take(3), [TodayCardId.summary, TodayCardId.people, TodayCardId.agenda]);
      await store.setVisible(TodayCardId.news, false);
      expect(await store.addFeed('https://a.example/rss'), isTrue);
      expect(await store.addFeed('nope'), isFalse);
      expect(await store.setCompanion(TodayCardId.pondering, url: 'https://orb.example/', token: 'secret'), isTrue);
      expect(secrets.values[TodayCardId.pondering], 'secret');

      final reloaded = TodayFeedSettingsStore(secrets: secrets);
      await reloaded.ensureLoaded();
      expect(reloaded.value.order.first, TodayCardId.summary);
      expect(reloaded.value.hidden, {TodayCardId.news});
      expect(reloaded.value.feeds, ['https://a.example/rss']);
      expect(reloaded.value.pondering.url, 'https://orb.example');
      expect(reloaded.value.pondering.hasToken, isTrue);
    });
  });

  group('CompanionClients', () {
    test('Coinpile summary with the bearer token', () async {
      late http.Request seen;
      final client = MockClient((request) async {
        seen = request;
        return http.Response(
            jsonEncode({
              'as_of': '2026-11-26',
              'net_worth': 12345.5,
              'bills_30d': 800,
              'savings_free': 2000,
              'spend_month_so_far': 950.25,
              'next_bill': {'name': 'Rent', 'date': '2026-12-01', 'amount': 1200},
              'alerts_total': 2,
              'alerts_critical': 1,
            }),
            200);
      });
      final result = await CompanionClients(client: client).coinpile('https://coinpile.example', 'cp_me_x');
      expect(seen.url.toString(), 'https://coinpile.example/api/v1/me/summary');
      expect(seen.headers['Authorization'], 'Bearer cp_me_x');
      final summary = (result as CompanionData<CoinpileSummary>).data;
      expect(summary.netWorth, 12345.5);
      expect(summary.nextBillName, 'Rent');
      expect(summary.alertsCritical, 1);
    });

    test('a refused token, a missing brief and a dead host are told apart', () async {
      Future<CompanionResult<PonderingBrief>> orb(http.Response Function(http.Request) answer) =>
          CompanionClients(client: MockClient((r) async => answer(r)))
              .pondering('https://orb.example', 't', day: '2026-11-26');
      expect(await orb((_) => http.Response('{}', 401)),
          isA<CompanionFailure<PonderingBrief>>().having((f) => f.kind, 'kind', 'unauthorized'));
      expect(await orb((_) => http.Response('{"error":"Not found."}', 404)), isA<CompanionNothing<PonderingBrief>>());
      expect(await orb((_) => throw http.ClientException('down')),
          isA<CompanionFailure<PonderingBrief>>().having((f) => f.kind, 'kind', 'unreachable'));
      final brief = await orb((r) {
        expect(r.url.path, '/api/c/daily/2026-11-26');
        return http.Response(
            jsonEncode(
                {'date': '2026-11-26', 'brief': '# Good morning\n\n**Today**\n- Dentist at 14:00', 'generatedAt': 1}),
            200);
      });
      expect((brief as CompanionData<PonderingBrief>).data.text, startsWith('# Good morning'));
    });

    test('brief markdown becomes plain lines', () {
      expect(PonderingBrief.plainLines('# Hi\n\n**Today**\n- Dentist at *14:00*\n[link](https://x)'),
          ['Hi', 'Today', '• Dentist at 14:00', 'link']);
    });
  });

  group('Today models and the overview widget document', () {
    final now = DateTime(2026, 11, 26, 9);
    final en = lookupAppLocalizations(const Locale('en'));
    late final dates = OmiDateFormat(locale: const Locale('en'), use24HourFormat: true, l10n: en);

    TodayAgenda agenda() => TodayAgenda.build(
          deviceEvents: [
            {
              'title': 'Standup',
              'startMs': DateTime(2026, 11, 26, 10).millisecondsSinceEpoch,
              'endMs': DateTime(2026, 11, 26, 10, 15).millisecondsSinceEpoch
            },
            {
              'title': 'Ended',
              'startMs': DateTime(2026, 11, 26, 7).millisecondsSinceEpoch,
              'endMs': DateTime(2026, 11, 26, 8).millisecondsSinceEpoch
            },
            {'title': 'Holiday', 'startMs': DateTime(2026, 11, 26).millisecondsSinceEpoch, 'allDay': true},
          ],
          family: FamilyDay(configured: true, reachable: true, next: [
            FamilyDayBlock(title: 'Standup', start: DateTime(2026, 11, 26, 10).toUtc(), timeLabel: '10:00'),
            FamilyDayBlock(title: 'Pickup', start: DateTime(2026, 11, 26, 15).toUtc(), timeLabel: '15:00'),
          ]),
          tasks: [
            ActionItemWithMetadata(
                id: 't1', description: 'Pay rent', completed: false, dueAt: DateTime(2026, 11, 26, 17)),
            ActionItemWithMetadata(
                id: 't0', description: 'Overdue', completed: false, dueAt: DateTime(2026, 11, 25, 17)),
            ActionItemWithMetadata(
                id: 't2', description: 'Tomorrow', completed: false, dueAt: DateTime(2026, 11, 27, 9)),
            ActionItemWithMetadata(id: 't3', description: 'Done', completed: true, dueAt: DateTime(2026, 11, 26, 12)),
          ],
          now: now,
        );

    test('agenda: all-day first, ended events out, a synced family block once, tasks due by today', () {
      final a = agenda();
      expect(a.events.map((e) => e.title), ['Holiday', 'Standup', 'Pickup']);
      expect(a.events.last.family, isTrue);
      expect(a.tasks.map((t) => t.id), ['t0', 't1']);
      expect(a.familyUnreachable, isFalse);
    });

    test('an unreachable family calendar is a soft note, not an error', () {
      final a = TodayAgenda.build(
          deviceEvents: const [],
          family: const FamilyDay(configured: true, reachable: false),
          tasks: const [],
          now: now);
      expect(a.isEmpty, isTrue);
      expect(a.familyUnreachable, isTrue);
    });

    test('the overview document carries every section, localized', () {
      final summary = TodayDaySummary.from([
        ServerConversation(
          id: 'c1',
          createdAt: DateTime(2026, 11, 26, 8),
          startedAt: DateTime(2026, 11, 26, 8),
          structured: Structured('Morning plan', 'Planned the week with the team.'),
          status: ConversationStatus.completed,
        ),
      ], now: now);
      final doc = AndroidWidgetDocuments.overview(
        agenda: agenda(),
        summary: summary,
        headlines: const [
          FeedItem(title: 'Big news', source: 'Daily Wire'),
          FeedItem(title: 'B'),
          FeedItem(title: 'C')
        ],
        l10n: en,
        dates: dates,
        now: now,
      );
      expect(doc['title'], 'Omi Today');
      expect(doc['day'], '2026-11-26');
      expect((doc['events'] as List).map((e) => (e as Map)['title']), ['Holiday', 'Standup', 'Pickup']);
      expect(((doc['events'] as List)[0] as Map)['detail'], 'All day');
      expect(((doc['events'] as List)[1] as Map)['detail'], '10:00');
      expect(((doc['tasks'] as List)[0] as Map)['detail'], 'Overdue');
      expect(doc['summary'], 'Planned the week with the team.');
      expect((doc['headlines'] as List), hasLength(2));
      expect(AndroidWidgetDocuments.keys, contains(AndroidWidgetDocuments.overviewKey));
    });
  });
}
