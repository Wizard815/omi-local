import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/services/today/feed_parser.dart';

/// Headlines from the user's feeds, merged newest first.
class NewsDigest {
  const NewsDigest({required this.items, required this.failedFeeds, this.fetchedAt});

  final List<FeedItem> items;

  /// Feeds that could not be read this time (the rest still show).
  final List<String> failedFeeds;
  final DateTime? fetchedAt;

  Map<String, Object?> toJson() => {
        'items': [for (final item in items) item.toJson()],
        'failed': failedFeeds,
        'at': fetchedAt?.millisecondsSinceEpoch,
      };

  static NewsDigest? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final items = <FeedItem>[
      if (raw['items'] case final List list)
        for (final entry in list)
          if (FeedItem.fromJson(entry) case final item?) item,
    ];
    final at = raw['at'];
    return NewsDigest(
      items: items,
      failedFeeds: [if (raw['failed'] case final List list) ...list.map((e) => '$e')],
      fetchedAt: at is int ? DateTime.fromMillisecondsSinceEpoch(at) : null,
    );
  }
}

/// Fetches the feeds straight from the phone (no Omi server involved) and keeps the last digest
/// in SharedPreferences, so the page and the Today overview widget show headlines at once.
class NewsFeedService {
  NewsFeedService({http.Client? client, DateTime Function()? now, this.timeout = const Duration(seconds: 12)})
      : _client = client,
        _now = now ?? DateTime.now;

  static final NewsFeedService instance = NewsFeedService();

  static const cacheKey = 'omi_today_news_v1';
  static const freshFor = Duration(minutes: 30);
  static const maxItems = 40;

  /// Feed bodies past this size are not read (a headline list is never this big).
  static const maxBytes = 2 * 1024 * 1024;

  final http.Client? _client;
  final DateTime Function() _now;
  final Duration timeout;
  Future<NewsDigest>? _inFlight;
  String _inFlightKey = '';

  Future<NewsDigest?> cached() async {
    try {
      final raw = (await SharedPreferences.getInstance()).getString(cacheKey);
      return raw == null ? null : NewsDigest.fromJson(jsonDecode(raw));
    } catch (_) {
      return null;
    }
  }

  /// The digest for [feeds]: the cache while fresh (unless [force]), else a new fetch.
  Future<NewsDigest> load(List<String> feeds, {bool force = false}) async {
    if (feeds.isEmpty) return const NewsDigest(items: [], failedFeeds: []);
    if (!force) {
      final cache = await cached();
      final at = cache?.fetchedAt;
      if (cache != null && at != null && _now().difference(at) < freshFor) return cache;
    }
    final key = feeds.join('\n');
    if (_inFlight != null && _inFlightKey == key) return _inFlight!;
    _inFlightKey = key;
    final run = _inFlight = _fetchAll(feeds);
    try {
      return await run;
    } finally {
      if (identical(_inFlight, run)) _inFlight = null;
    }
  }

  Future<NewsDigest> _fetchAll(List<String> feeds) async {
    final client = _client ?? http.Client();
    try {
      final results = await Future.wait(feeds.map((url) => _fetchOne(client, url)));
      final failed = <String>[];
      final items = <FeedItem>[];
      for (var i = 0; i < feeds.length; i++) {
        final parsed = results[i];
        if (parsed == null) {
          failed.add(feeds[i]);
        } else {
          items.addAll(parsed.items);
        }
      }
      final digest = NewsDigest(items: merge(items), failedFeeds: failed, fetchedAt: _now());
      if (failed.length < feeds.length) {
        try {
          final prefs = await SharedPreferences.getInstance();
          await prefs.setString(cacheKey, jsonEncode(digest.toJson()));
        } catch (_) {}
      }
      return digest;
    } finally {
      if (_client == null) client.close();
    }
  }

  Future<ParsedFeed?> _fetchOne(http.Client client, String url) async {
    try {
      final response = await client.get(Uri.parse(url), headers: const {
        'Accept': 'application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5',
      }).timeout(timeout);
      if (response.statusCode < 200 || response.statusCode >= 300) return null;
      if (response.bodyBytes.length > maxBytes) return null;
      final body = utf8.decode(response.bodyBytes, allowMalformed: true);
      return FeedParser.parse(body, fallbackTitle: Uri.parse(url).host);
    } catch (_) {
      return null;
    }
  }

  /// Newest first (undated last, in feed order), one row per title, at most [maxItems].
  static List<FeedItem> merge(List<FeedItem> items) {
    final seen = <String>{};
    final unique = [
      for (final item in items)
        if (seen.add(item.title.toLowerCase())) item,
    ];
    final dated = unique.where((i) => i.published != null).toList()
      ..sort((a, b) => b.published!.compareTo(a.published!));
    return [...dated, ...unique.where((i) => i.published == null)].take(maxItems).toList();
  }
}
