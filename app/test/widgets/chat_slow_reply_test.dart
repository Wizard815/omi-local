import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/chat/widgets/ai_message.dart';
import 'package:omi/ui/ui.dart';

Widget _app(Widget child) => MaterialApp(
      theme: buildOmiTheme(),
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: child),
    );

AppLocalizations _l10n(WidgetTester tester) => AppLocalizations.of(tester.element(find.byType(Scaffold)));

void main() {
  group('visibleChatSteps', () {
    test('drops the server keep-alive and a step repeated back to back', () {
      expect(
        visibleChatSteps([
          'Searching conversations',
          chatProgressHeartbeat,
          chatProgressHeartbeat,
          'Searching conversations',
          'Loaded calendar',
          '',
          chatProgressHeartbeat,
        ]),
        ['Searching conversations', 'Loaded calendar'],
      );
    });

    test('keeps a step that comes back after another one', () {
      expect(visibleChatSteps(['A', 'B', 'A']), ['A', 'B', 'A']);
    });
  });

  testWidgets('a reply waiting minutes on a slow model shows one step line, never a list of keep-alives',
      (tester) async {
    await tester.pumpWidget(_app(const ChatActivitySteps(
      thinkings: ['Searching conversations', chatProgressHeartbeat, chatProgressHeartbeat, chatProgressHeartbeat],
      working: true,
    )));
    expect(find.text('Searching conversations'), findsOneWidget);
    expect(find.text(chatProgressHeartbeat), findsNothing);
  });

  testWidgets('the still-working line appears only after the wait gets long', (tester) async {
    await tester.pumpWidget(_app(ChatSlowReplyNotice(startedAt: DateTime.now())));
    expect(find.byKey(const ValueKey('chat_slow_reply_notice')), findsNothing);

    await tester.pumpWidget(
      _app(ChatSlowReplyNotice(startedAt: DateTime.now().subtract(const Duration(minutes: 3)))),
    );
    expect(find.byKey(const ValueKey('chat_slow_reply_notice')), findsOneWidget);
    expect(find.text(_l10n(tester).chatReplyStillWorking), findsOneWidget);
  });

  testWidgets('the still-working line arrives on its own while the reader waits', (tester) async {
    await tester.pumpWidget(_app(ChatSlowReplyNotice(
      startedAt: DateTime.now(),
      after: const Duration(milliseconds: 1),
    )));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
    await tester.pump(const Duration(seconds: 2));
    expect(find.byKey(const ValueKey('chat_slow_reply_notice')), findsOneWidget);
  });
}
