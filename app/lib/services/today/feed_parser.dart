import 'package:intl/intl.dart';
import 'package:xml/xml.dart';

/// One headline from a news feed.
class FeedItem {
  const FeedItem({required this.title, this.link, this.published, this.source = ''});

  final String title;
  final String? link;

  /// UTC, when the feed gave a date it could be read.
  final DateTime? published;

  /// The feed's own title ("BBC News").
  final String source;

  Map<String, Object?> toJson() => {
        'title': title,
        'link': link,
        'published': published?.millisecondsSinceEpoch,
        'source': source,
      };

  static FeedItem? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final title = raw['title'];
    if (title is! String || title.trim().isEmpty) return null;
    final ms = raw['published'];
    return FeedItem(
      title: title,
      link: raw['link'] is String ? raw['link'] as String : null,
      published: ms is int ? DateTime.fromMillisecondsSinceEpoch(ms, isUtc: true) : null,
      source: raw['source'] is String ? raw['source'] as String : '',
    );
  }
}

class ParsedFeed {
  const ParsedFeed({required this.title, required this.items});

  final String title;
  final List<FeedItem> items;
}

/// Thrown for a document that is not RSS 2.0, RSS 1.0 (RDF) or Atom.
class FeedFormatException implements Exception {
  const FeedFormatException(this.message);
  final String message;

  @override
  String toString() => 'FeedFormatException: $message';
}

/// A small RSS/Atom reader: titles, links and dates only (Omi Today shows headlines, never the
/// article). Pure, so it is tested against fixture documents.
abstract final class FeedParser {
  static const maxItems = 30;

  static ParsedFeed parse(String body, {String fallbackTitle = ''}) {
    final XmlDocument doc;
    try {
      doc = XmlDocument.parse(body);
    } on XmlException catch (e) {
      throw FeedFormatException('not XML: ${e.message}');
    }
    final root = doc.rootElement;
    switch (root.localName) {
      case 'rss':
        final channel = _child(root, 'channel');
        if (channel == null) throw const FeedFormatException('RSS without a channel');
        final title = _text(_child(channel, 'title')) ?? fallbackTitle;
        return ParsedFeed(
            title: title, items: _items(channel.childElements.where((e) => e.localName == 'item'), title));
      case 'RDF':
        final channel = _child(root, 'channel');
        final title = _text(channel == null ? null : _child(channel, 'title')) ?? fallbackTitle;
        return ParsedFeed(title: title, items: _items(root.childElements.where((e) => e.localName == 'item'), title));
      case 'feed':
        final title = _text(_child(root, 'title')) ?? fallbackTitle;
        final entries = root.childElements.where((e) => e.localName == 'entry');
        final items = <FeedItem>[];
        for (final entry in entries) {
          final itemTitle = _text(_child(entry, 'title'));
          if (itemTitle == null) continue;
          items.add(FeedItem(
            title: itemTitle,
            link: _atomLink(entry),
            published: parseDate(_text(_child(entry, 'published')) ?? _text(_child(entry, 'updated'))),
            source: title,
          ));
          if (items.length == maxItems) break;
        }
        return ParsedFeed(title: title, items: items);
      default:
        throw FeedFormatException('unknown root <${root.localName}>');
    }
  }

  static List<FeedItem> _items(Iterable<XmlElement> elements, String source) {
    final items = <FeedItem>[];
    for (final item in elements) {
      final title = _text(_child(item, 'title'));
      if (title == null) continue;
      items.add(FeedItem(
        title: title,
        link: _text(_child(item, 'link')) ?? _text(_child(item, 'guid')),
        published: parseDate(_text(_child(item, 'pubDate')) ?? _text(_child(item, 'date'))),
        source: source,
      ));
      if (items.length == maxItems) break;
    }
    return items;
  }

  static String? _atomLink(XmlElement entry) {
    String? fallback;
    for (final link in entry.childElements.where((e) => e.localName == 'link')) {
      final href = link.getAttribute('href')?.trim();
      if (href == null || href.isEmpty) continue;
      final rel = link.getAttribute('rel');
      if (rel == null || rel == 'alternate') return href;
      fallback ??= href;
    }
    return fallback;
  }

  static XmlElement? _child(XmlElement parent, String localName) {
    for (final element in parent.childElements) {
      if (element.localName == localName) return element;
    }
    return null;
  }

  /// The element's text with markup and runs of whitespace collapsed; null when empty.
  static String? _text(XmlElement? element) {
    if (element == null) return null;
    final text = element.innerText.replaceAll(RegExp(r'<[^>]*>'), ' ').replaceAll(RegExp(r'\s+'), ' ').trim();
    return text.isEmpty ? null : text;
  }

  static final _rfc822 =
      DateFormat('EEE, d MMM yyyy HH:mm:ss', 'en_US'); // omi-ux-allow: date-pattern -- parses RSS dates, never shown
  static final _rfc822NoDay =
      DateFormat('d MMM yyyy HH:mm:ss', 'en_US'); // omi-ux-allow: date-pattern -- parses RSS dates, never shown
  static final _zone = RegExp(r'\s*([+-]\d{2}:?\d{2}|[A-Z]{1,4})$');
  static const _zones = {
    'GMT': 0,
    'UTC': 0,
    'UT': 0,
    'Z': 0,
    'EST': -5,
    'EDT': -4,
    'CST': -6,
    'CDT': -5,
    'MST': -7,
    'MDT': -6,
    'PST': -8,
    'PDT': -7
  };

  /// RFC 3339 / ISO 8601 (Atom, Dublin Core) or RFC 822 (RSS) to UTC; null when unreadable.
  static DateTime? parseDate(String? raw) {
    if (raw == null) return null;
    final text = raw.trim();
    if (text.isEmpty) return null;
    final iso = DateTime.tryParse(text);
    if (iso != null) return iso.toUtc();
    var offset = Duration.zero;
    var body = text;
    final zone = _zone.firstMatch(text);
    if (zone != null) {
      final z = zone.group(1)!;
      if (z.startsWith('+') || z.startsWith('-')) {
        final digits = z.replaceAll(':', '');
        final sign = digits.startsWith('-') ? -1 : 1;
        offset = Duration(hours: int.parse(digits.substring(1, 3)), minutes: int.parse(digits.substring(3, 5))) * sign;
      } else if (_zones.containsKey(z)) {
        offset = Duration(hours: _zones[z]!);
      }
      body = text.substring(0, zone.start).trim();
    }
    // Some feeds leave out the seconds ("Tue, 3 Jun 2026 09:30").
    if (RegExp(r' \d{1,2}:\d{2}$').hasMatch(body)) body = '$body:00';
    for (final format in [_rfc822, _rfc822NoDay]) {
      try {
        return format.parseStrict(body, true).subtract(offset);
      } on FormatException {
        continue;
      }
    }
    return null;
  }
}
