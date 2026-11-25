import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/chat/widgets/ai_message.dart';
import 'package:omi/pages/chat/widgets/phone_llm_section.dart';
import 'package:omi/services/phone_llm/phone_llm_client.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';
import 'package:omi/ui/ui.dart';

import '../../helpers/sfpro_font_fixtures.dart';

/// Renders "AI on this phone" and the phone-answer states on a phone-sized screen with the app's
/// real fonts and asserts each lays out without overflow. With
/// `--dart-define=PHONE_LLM_RENDER_OUTPUT=<dir>` it also writes the PNGs used as PR evidence.
Future<void> _loadFonts() async {
  final manifest = jsonDecode(await rootBundle.loadString('FontManifest.json')) as List;
  for (final family in manifest) {
    final loader = FontLoader(family['family'] as String);
    for (final font in family['fonts'] as List) {
      loader.addFont(rootBundle.load(font['asset'] as String));
    }
    await loader.load();
  }
  for (final family in ['Roboto', 'FlutterTest', 'SF Pro Display']) {
    final loader = FontLoader(family);
    for (final font in ['REGULAR', 'MEDIUM', 'BOLD']) {
      loader.addFont(sfProFont('SFPRODISPLAY$font.OTF'));
    }
    await loader.load();
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(_loadFonts);
  const output = String.fromEnvironment('PHONE_LLM_RENDER_OUTPUT');
  final boundary = GlobalKey();

  Future<void> shoot(WidgetTester tester, String name) async {
    expect(tester.takeException(), isNull, reason: '$name rendered with an exception');
    if (output.isEmpty) return;
    await tester.runAsync(() async {
      final render = boundary.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      final image = await render.toImage(pixelRatio: 2);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory(output).create(recursive: true);
      await File('$output/$name.png').writeAsBytes(bytes!.buffer.asUint8List());
    });
  }

  Future<void> pump(WidgetTester tester, Widget body, {String? title}) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    OmiColors.active = OmiColors.forBrightness(Brightness.dark);
    final theme = buildOmiTheme(brightness: Brightness.dark);
    await tester.pumpWidget(RepaintBoundary(
      key: boundary,
      child: MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: theme.copyWith(
          appBarTheme: theme.appBarTheme.copyWith(
            titleTextStyle: theme.appBarTheme.titleTextStyle?.copyWith(fontFamily: 'SF Pro Display'),
          ),
        ),
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(
          appBar: AppBar(leading: const OmiBackButton(), title: title == null ? null : Text(title)),
          body: ListView(padding: const EdgeInsets.all(OmiSpacing.lg), children: [body]),
        ),
      ),
    ));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
  }

  testWidgets('1 AI on this phone (Android settings)', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final settings = PhoneLlmSettings(secrets: MemorySecretStore('k-render'));
    await tester.runAsync(() async {
      await settings.ensureLoaded();
      await settings.setMode(PhoneLlmMode.auto);
    });
    await pump(
      tester,
      PhoneLlmSection(
        isAndroid: true,
        settings: settings,
        clientFor: (s) => PhoneLlmClient(
          baseUrl: s.baseUrl,
          client: MockClient((_) async => http.Response(
              jsonEncode({
                'data': [
                  {'id': 'small-q4'},
                ],
              }),
              200)),
        ),
      ),
      title: 'AI on this phone',
    );
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('small-q4'), findsOneWidget);
    await shoot(tester, '001-mobile-android-phone-llm-settings');
  });

  testWidgets('2 a phone answer and a failed server reply', (tester) async {
    await pump(
      tester,
      Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Your dentist appointment is on Tuesday at 9.', style: OmiType.body),
          const PhoneAnswerLabel(model: 'small-q4'),
          const SizedBox(height: OmiSpacing.xl),
          ChatReplyError(onRetry: () {}, onAskPhone: () {}),
        ],
      ),
    );
    await shoot(tester, '002-mobile-android-phone-llm-chat');
  });
}
