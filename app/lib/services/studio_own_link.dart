import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';

/// "Send to Hermes" (Android): a conversation's title and overview into a new chat in the Studio
/// Own app (`com.ekkostudio.own`), unsent, or the share sheet when that app is not installed.
/// Contract: hermes-studio `docs/omi.md`. Nothing is sent without the person pressing Send there.
abstract final class StudioOwnLink {
  static const package = 'com.ekkostudio.own';

  /// Studio Own cuts longer text.
  static const maxChars = 20000;

  static String text(String title, String overview) {
    final text = [title.trim(), overview.trim()].where((s) => s.isNotEmpty).join('\n\n');
    return text.length <= maxChars ? text : text.substring(0, maxChars);
  }

  /// `ekkostudio-own://chat?text=…`, form-encoded (a space is `+`, a literal `+` is `%2B`).
  static Uri chatUri(String text) => Uri(scheme: 'ekkostudio-own', host: 'chat', queryParameters: {'text': text});

  /// Opens Studio Own on a new chat holding [title] and [overview]; falls back to the share sheet.
  static Future<void> send(
    String title,
    String overview, {
    Future<bool> Function(Uri uri)? canOpen,
    Future<bool> Function(Uri uri)? open,
    Future<void> Function(String text, String subject)? share,
  }) async {
    final body = text(title, overview);
    final uri = chatUri(body);
    try {
      if (await (canOpen ?? canLaunchUrl)(uri) &&
          await (open ?? (u) => launchUrl(u, mode: LaunchMode.externalApplication))(uri)) {
        return;
      }
    } catch (_) {
      // Not installed or not answering: the share sheet below.
    }
    await (share ?? _share)(body, title.trim());
  }

  static Future<void> _share(String text, String subject) async {
    await SharePlus.instance.share(ShareParams(text: text, subject: subject.isEmpty ? null : subject));
  }
}
