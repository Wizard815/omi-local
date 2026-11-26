import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The cards of Omi Today, in their default order.
enum TodayCardId {
  agenda,
  summary,
  people,
  news,
  coinpile,
  pondering;

  static TodayCardId? fromName(String name) {
    for (final id in values) {
      if (id.name == name) return id;
    }
    return null;
  }
}

/// Where a companion card (Coinpile, Pondering Orb) reads from. The token lives in secure storage,
/// never in this value.
@immutable
class CompanionEndpoint {
  const CompanionEndpoint({this.url = '', this.hasToken = false});

  final String url;
  final bool hasToken;

  bool get configured => url.trim().isNotEmpty;
}

/// What Omi Today shows: card order, hidden cards, news feeds, companion addresses. Kept on this
/// phone only.
@immutable
class TodayFeedSettings {
  const TodayFeedSettings({
    this.order = TodayCardId.values,
    this.hidden = const {},
    this.feeds = const [],
    this.coinpile = const CompanionEndpoint(),
    this.pondering = const CompanionEndpoint(),
  });

  /// Every card exactly once, in the order they show.
  final List<TodayCardId> order;
  final Set<TodayCardId> hidden;

  /// RSS or Atom feed addresses.
  final List<String> feeds;
  final CompanionEndpoint coinpile;
  final CompanionEndpoint pondering;

  static const maxFeeds = 20;

  List<TodayCardId> get visible => order.where((id) => !hidden.contains(id)).toList();

  /// [order] with any missing card appended and duplicates dropped, so a saved order from an older
  /// build still lists every card.
  static List<TodayCardId> normalizeOrder(Iterable<TodayCardId> order) {
    final out = <TodayCardId>[];
    for (final id in [...order, ...TodayCardId.values]) {
      if (!out.contains(id)) out.add(id);
    }
    return out;
  }

  /// A feed address worth fetching: http(s) with a host. Returns it trimmed, or null.
  static String? cleanFeedUrl(String raw) {
    final text = raw.trim();
    final uri = Uri.tryParse(text);
    if (uri == null || !(uri.scheme == 'https' || uri.scheme == 'http') || uri.host.isEmpty) return null;
    return text;
  }

  /// A companion base address without a trailing slash, or null when it is not http(s).
  static String? cleanBaseUrl(String raw) {
    var text = raw.trim();
    if (text.isEmpty) return '';
    final uri = Uri.tryParse(text);
    if (uri == null || !(uri.scheme == 'https' || uri.scheme == 'http') || uri.host.isEmpty) return null;
    while (text.endsWith('/')) {
      text = text.substring(0, text.length - 1);
    }
    return text;
  }

  TodayFeedSettings copyWith({
    List<TodayCardId>? order,
    Set<TodayCardId>? hidden,
    List<String>? feeds,
    CompanionEndpoint? coinpile,
    CompanionEndpoint? pondering,
  }) =>
      TodayFeedSettings(
        order: order ?? this.order,
        hidden: hidden ?? this.hidden,
        feeds: feeds ?? this.feeds,
        coinpile: coinpile ?? this.coinpile,
        pondering: pondering ?? this.pondering,
      );

  Map<String, Object?> toJson() => {
        'order': [for (final id in order) id.name],
        'hidden': [for (final id in hidden) id.name],
        'feeds': feeds,
        'coinpile_url': coinpile.url,
        'pondering_url': pondering.url,
      };

  static TodayFeedSettings fromJson(Object? raw, {bool coinpileToken = false, bool ponderingToken = false}) {
    if (raw is! Map) {
      return TodayFeedSettings(
        coinpile: CompanionEndpoint(hasToken: coinpileToken),
        pondering: CompanionEndpoint(hasToken: ponderingToken),
      );
    }
    List<TodayCardId> ids(Object? list) => [
          if (list is List)
            for (final name in list)
              if (TodayCardId.fromName('$name') case final id?) id,
        ];
    final feeds = <String>[
      if (raw['feeds'] case final List list)
        for (final url in list)
          if (cleanFeedUrl('$url') case final clean?) clean,
    ];
    return TodayFeedSettings(
      order: normalizeOrder(ids(raw['order'])),
      hidden: ids(raw['hidden']).toSet(),
      feeds: feeds.toSet().take(maxFeeds).toList(),
      coinpile: CompanionEndpoint(url: '${raw['coinpile_url'] ?? ''}', hasToken: coinpileToken),
      pondering: CompanionEndpoint(url: '${raw['pondering_url'] ?? ''}', hasToken: ponderingToken),
    );
  }
}

/// Secrets for the companion cards (a Coinpile read token, a Pondering Orb API token).
abstract class TodaySecretStore {
  Future<String?> read(TodayCardId card);
  Future<void> write(TodayCardId card, String? value);
}

class SecureTodaySecretStore implements TodaySecretStore {
  const SecureTodaySecretStore();

  static const _storage = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));

  String _key(TodayCardId card) => 'today_token_${card.name}';

  @override
  Future<String?> read(TodayCardId card) => _storage.read(key: _key(card));

  @override
  Future<void> write(TodayCardId card, String? value) => value == null || value.trim().isEmpty
      ? _storage.delete(key: _key(card))
      : _storage.write(key: _key(card), value: value.trim());
}

class MemoryTodaySecretStore implements TodaySecretStore {
  final Map<TodayCardId, String> values = {};

  @override
  Future<String?> read(TodayCardId card) async => values[card];

  @override
  Future<void> write(TodayCardId card, String? value) async {
    if (value == null || value.trim().isEmpty) {
      values.remove(card);
    } else {
      values[card] = value.trim();
    }
  }
}

/// Loads and saves [TodayFeedSettings]; the page and its settings listen to it.
class TodayFeedSettingsStore extends ChangeNotifier {
  TodayFeedSettingsStore({TodaySecretStore? secrets}) : secrets = secrets ?? const SecureTodaySecretStore();

  static final TodayFeedSettingsStore instance = TodayFeedSettingsStore();

  static const prefsKey = 'omi_today_settings_v1';

  final TodaySecretStore secrets;
  TodayFeedSettings _value = const TodayFeedSettings();
  bool _loaded = false;

  TodayFeedSettings get value => _value;
  bool get loaded => _loaded;

  Future<void> ensureLoaded() async {
    if (_loaded) return;
    final prefs = await SharedPreferences.getInstance();
    Object? raw;
    try {
      final text = prefs.getString(prefsKey);
      raw = text == null ? null : jsonDecode(text);
    } catch (_) {
      raw = null;
    }
    bool hasToken(String? token) => token != null && token.isNotEmpty;
    final coinpileToken = await _safeRead(TodayCardId.coinpile);
    final ponderingToken = await _safeRead(TodayCardId.pondering);
    _value = TodayFeedSettings.fromJson(raw,
        coinpileToken: hasToken(coinpileToken), ponderingToken: hasToken(ponderingToken));
    _loaded = true;
    notifyListeners();
  }

  Future<String?> _safeRead(TodayCardId card) async {
    try {
      return await secrets.read(card);
    } catch (_) {
      return null;
    }
  }

  Future<String?> token(TodayCardId card) => _safeRead(card);

  Future<void> _save(TodayFeedSettings next) async {
    _value = next;
    _loaded = true;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(prefsKey, jsonEncode(next.toJson()));
  }

  /// Moves the card at [oldIndex] to [newIndex], an index in the list without it (what
  /// `onReorderItem` gives).
  Future<void> reorder(int oldIndex, int newIndex) {
    final order = [..._value.order];
    final moved = order.removeAt(oldIndex);
    order.insert(newIndex.clamp(0, order.length), moved);
    return _save(_value.copyWith(order: order));
  }

  Future<void> setVisible(TodayCardId card, bool visible) {
    final hidden = {..._value.hidden};
    visible ? hidden.remove(card) : hidden.add(card);
    return _save(_value.copyWith(hidden: hidden));
  }

  /// False when [url] is not an http(s) address or the list is full; a repeat is a no-op.
  Future<bool> addFeed(String url) async {
    final clean = TodayFeedSettings.cleanFeedUrl(url);
    if (clean == null || _value.feeds.length >= TodayFeedSettings.maxFeeds) return false;
    if (_value.feeds.contains(clean)) return true;
    await _save(_value.copyWith(feeds: [..._value.feeds, clean]));
    return true;
  }

  Future<void> removeFeed(String url) => _save(_value.copyWith(feeds: [..._value.feeds]..remove(url)));

  Future<void> insertFeed(int index, String url) {
    if (_value.feeds.contains(url)) return Future.value();
    final feeds = [..._value.feeds]..insert(index.clamp(0, _value.feeds.length), url);
    return _save(_value.copyWith(feeds: feeds));
  }

  /// Saves a companion card's address and, when [token] is given, its token (empty clears it).
  /// False when [url] is not an http(s) address.
  Future<bool> setCompanion(TodayCardId card, {required String url, String? token}) async {
    final clean = TodayFeedSettings.cleanBaseUrl(url);
    if (clean == null) return false;
    var hasToken = (card == TodayCardId.coinpile ? _value.coinpile : _value.pondering).hasToken;
    if (token != null) {
      await secrets.write(card, token);
      hasToken = token.trim().isNotEmpty;
    }
    final endpoint = CompanionEndpoint(url: clean, hasToken: hasToken);
    await _save(
        card == TodayCardId.coinpile ? _value.copyWith(coinpile: endpoint) : _value.copyWith(pondering: endpoint));
    return true;
  }
}
