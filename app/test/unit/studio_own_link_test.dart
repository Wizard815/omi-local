import 'package:flutter_test/flutter_test.dart';

import 'package:omi/pages/home/home_deep_links.dart';
import 'package:omi/services/studio_own_link.dart';

/// Omi and the Studio Own app (com.ekkostudio.own; hermes-studio docs/omi.md): its omi:// links
/// open Omi on a screen, and "Send to Hermes" opens a new Studio chat with the conversation in it.
void main() {
  group('Studio Own → Omi links', () {
    test('approvals, tasks and capture open their place in Omi', () {
      expect(HomeDeepLink.studioRoute(Uri.parse('omi://approvals')), '/approvals');
      expect(HomeDeepLink.studioRoute(Uri.parse('omi://tasks')), '/action-items');
      expect(HomeDeepLink.studioRoute(Uri.parse('omi://capture')), '/new-memory');
      expect(HomeDeepLink.studioRoute(Uri.parse('omi://TASKS/')), '/action-items');
    });

    test('the routes are ones the home shell opens', () {
      expect(HomeDeepLink.parse('/approvals')!.alias, 'approvals');
      expect(HomeDeepLink.parse('/action-items')!.tabIndex, isNotNull);
      expect(HomeDeepLink.parse('/new-memory')!.alias, 'new-memory');
    });

    test('unknown hosts, other schemes and links with a path are not Studio links', () {
      expect(HomeDeepLink.studioRoute(Uri.parse('omi://settings')), isNull);
      expect(HomeDeepLink.studioRoute(Uri.parse('omi://app/approvals')), isNull);
      expect(HomeDeepLink.studioRoute(Uri.parse('omi://tasks/abc123')), isNull, reason: 'a shared-tasks link');
      expect(HomeDeepLink.studioRoute(Uri.parse('https://approvals/')), isNull);
      expect(HomeDeepLink.studioRoute(Uri.parse('omi-dev://approvals')), isNull);
    });

    test('the AI source widget routes', () {
      final next = HomeDeepLink.parse('/ai-source/next')!;
      expect(next.alias, 'ai-source');
      expect(next.id, 'next');
      expect(HomeDeepLink.parse('/ai-source')!.id, isNull);
    });
  });

  group('Send to Hermes', () {
    test('title and overview, form-encoded into ekkostudio-own://chat', () {
      final text = StudioOwnLink.text(' Standup ', 'Ship on Friday. C++ & 50% done ');
      expect(text, 'Standup\n\nShip on Friday. C++ & 50% done');
      final uri = StudioOwnLink.chatUri(text);
      expect(uri.scheme, 'ekkostudio-own');
      expect(uri.host, 'chat');
      expect(uri.toString(), contains('C%2B%2B'), reason: 'a literal + is %2B; + means a space');
      expect(uri.queryParameters['text'], text);
    });

    test('long text is cut at Studio Own\'s limit; an empty title is left out', () {
      expect(StudioOwnLink.text('', 'x' * 30000).length, StudioOwnLink.maxChars);
      expect(StudioOwnLink.text('', 'Only overview'), 'Only overview');
    });

    test('opens Studio Own when it is installed', () async {
      final opened = <Uri>[];
      var shared = 0;
      await StudioOwnLink.send('T', 'O',
          canOpen: (_) async => true,
          open: (u) async {
            opened.add(u);
            return true;
          },
          share: (_, __) async => shared++);
      expect(opened.single.host, 'chat');
      expect(shared, 0);
    });

    test('falls back to the share sheet when Studio Own is missing or does not open', () async {
      final shares = <String>[];
      Future<void> share(String text, String subject) async => shares.add('$subject|$text');
      await StudioOwnLink.send('T', 'O', canOpen: (_) async => false, open: (_) async => true, share: share);
      await StudioOwnLink.send('T', 'O', canOpen: (_) async => true, open: (_) async => false, share: share);
      await StudioOwnLink.send('T', 'O',
          canOpen: (_) async => throw StateError('no'), open: (_) async => true, share: share);
      expect(shares, ['T|T\n\nO', 'T|T\n\nO', 'T|T\n\nO']);
    });
  });
}
