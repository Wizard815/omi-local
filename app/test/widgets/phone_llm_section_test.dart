/// "AI on this phone": the setting (Android only), the phone-answer label, and "Ask this phone
/// instead" on a failed reply.
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/chat/widgets/ai_message.dart';
import 'package:omi/pages/chat/widgets/phone_llm_section.dart';
import 'package:omi/pages/home/home_deep_links.dart';
import 'package:omi/services/phone_llm/phone_llm_client.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';

Widget _host(Widget child) => MaterialApp(
      locale: const Locale('en'),
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: SingleChildScrollView(child: child)),
    );

void main() {
  late PhoneLlmSettings settings;
  late int modelsStatus;
  late List<String> models;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    settings = PhoneLlmSettings(secrets: MemorySecretStore());
    modelsStatus = 200;
    models = ['small-q4'];
  });

  PhoneLlmClient client(PhoneLlmSettings s) => PhoneLlmClient(
        baseUrl: s.baseUrl,
        client: MockClient((request) async {
          if (request.url.path.endsWith('/models')) {
            return http.Response(
                jsonEncode({
                  'data': [
                    for (final id in models) {'id': id},
                  ],
                }),
                modelsStatus);
          }
          return http.Response('{"choices":[]}', 200);
        }),
      );

  Future<void> pumpSection(WidgetTester tester, {required bool android}) async {
    await tester.pumpWidget(_host(PhoneLlmSection(isAndroid: android, settings: settings, clientFor: client)));
    await tester.pumpAndSettle();
  }

  testWidgets('hidden off Android', (tester) async {
    await pumpSection(tester, android: false);
    expect(find.byKey(const ValueKey('phone_llm_section')), findsNothing);
    expect(find.text('AI on this phone'), findsNothing);
  });

  testWidgets('on Android: the three sources, the address, the model list and context toggle', (tester) async {
    await pumpSection(tester, android: true);

    expect(find.text('AI on this phone'), findsOneWidget);
    expect(find.text('Server'), findsOneWidget);
    expect(find.text('This phone'), findsOneWidget);
    expect(find.text("Phone when the server can't be reached"), findsOneWidget);
    expect(find.text('http://127.0.0.1:8081/v1'), findsOneWidget);
    expect(find.text('small-q4'), findsOneWidget);
    expect(settings.model, 'small-q4', reason: 'the only model on the phone is picked');
    expect(find.text('Include my recent conversations as context'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('phone_llm_mode_auto')));
    await tester.pumpAndSettle();
    expect(settings.mode, PhoneLlmMode.auto);

    await tester.ensureVisible(find.byKey(const ValueKey('phone_llm_include_conversations')));
    await tester.tap(find.byKey(const ValueKey('phone_llm_include_conversations')));
    await tester.pumpAndSettle();
    expect(settings.includeConversations, isTrue);
  });

  testWidgets('Test shows the round trip', (tester) async {
    await pumpSection(tester, android: true);
    await tester.ensureVisible(find.byKey(const ValueKey('phone_llm_test')));
    await tester.tap(find.byKey(const ValueKey('phone_llm_test')));
    await tester.pumpAndSettle();
    expect(find.textContaining(RegExp(r'^Answered in \d+ ms$')), findsOneWidget);
  });

  testWidgets('a phone model that refuses the key says so instead of listing models', (tester) async {
    modelsStatus = 401;
    await pumpSection(tester, android: true);
    expect(find.text('The phone model refused the API key.'), findsOneWidget);
    expect(settings.model, isEmpty);
  });

  testWidgets('the API key is typed into secure storage, not shown', (tester) async {
    await pumpSection(tester, android: true);
    await tester.enterText(find.byKey(const ValueKey('phone_llm_api_key')), 'k-typed');
    await tester.pump(const Duration(seconds: 1));
    expect(settings.apiKey, 'k-typed');
    final field = tester.widget<TextField>(find.byKey(const ValueKey('phone_llm_api_key')));
    expect(field.obscureText, isTrue);
  });

  testWidgets('the widget\'s Switch link moves on from the source it showed; a stale tap changes nothing',
      (tester) async {
    final previous = PhoneLlmSettings.instance;
    addTearDown(() => PhoneLlmSettings.instance = previous);
    PhoneLlmSettings.instance = settings;
    await tester.runAsync(settings.ensureLoaded);
    expect(settings.mode, PhoneLlmMode.server);
    late BuildContext context;
    await tester.pumpWidget(_host(Builder(builder: (c) {
      context = c;
      return const SizedBox();
    })));

    Future<void> open(String route) async {
      await tester.runAsync(() => openHomeDeepLink(context, HomeDeepLink.parse(route)!, openSettings: () async {}));
      await tester.pump();
    }

    await open('/ai-source/next?from=server');
    expect(settings.mode, PhoneLlmMode.phone);
    expect(find.text('AI source: This phone'), findsOneWidget);

    await open('/ai-source/next?from=server');
    expect(settings.mode, PhoneLlmMode.phone, reason: 'a repeated tap on the old widget does not skip ahead');

    await open('/ai-source/next?from=phone');
    expect(settings.mode, PhoneLlmMode.auto);
    await tester.pump(const Duration(seconds: 5));
  });

  testWidgets('a phone answer is labelled with its model and that it is not on the server', (tester) async {
    await tester.pumpWidget(_host(const PhoneAnswerLabel(model: 'small-q4')));
    expect(find.text('Answered on this phone (small-q4)'), findsOneWidget);
    expect(find.text('Not saved on your server. Kept until Omi closes.'), findsOneWidget);
  });

  testWidgets('a failed server reply offers Ask This Phone Instead next to Try Again', (tester) async {
    var asked = 0;
    await tester.pumpWidget(_host(ChatReplyError(onRetry: () {}, onAskPhone: () => asked++)));
    expect(find.text('Try Again'), findsOneWidget);
    await tester.tap(find.text('Ask This Phone Instead'));
    expect(asked, 1);
  });

  testWidgets('a failed phone reply says the phone model did not answer, without the phone action', (tester) async {
    await tester.pumpWidget(_host(ChatReplyError(onRetry: () {}, phoneError: PhoneLlmError.unreachable)));
    expect(find.text("The phone model didn't answer. Check that it's running."), findsOneWidget);
    expect(find.text('Ask This Phone Instead'), findsNothing);

    await tester.pumpWidget(_host(ChatReplyError(onRetry: () {}, phoneError: PhoneLlmError.unauthorized)));
    expect(find.text('The phone model refused the API key.'), findsOneWidget);
  });
}
