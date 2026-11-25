import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/backend/schema/local_usage.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/settings/server_usage_page.dart';
import 'package:omi/ui/ui.dart';

/// "Your usage on this server" reads the person's own counts from GET /v1/users/me/local-usage.
void main() {
  /// Decodes like the API: the server always sends enabled, retention_days and window_days.
  LocalUsageReport report(Map<String, dynamic> json) => LocalUsageReport.fromGenerated(
      wire.GeneratedLocalUsageResponse.fromJson({'enabled': true, 'retention_days': 90, 'window_days': 30, ...json}));

  setUpAll(() => initializeDateFormatting());

  Future<void> pump(WidgetTester tester, Future<ApiResult<LocalUsageReport>> Function() load) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildOmiTheme(),
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: const [Locale('en')],
      home: ServerUsagePage(load: load),
    ));
    await tester.pumpAndSettle();
  }

  group('LocalUsageReport', () {
    test('reads totals most used first and recent events newest first, skipping rows without a count, name or time',
        () {
      final result = report({
        'enabled': true,
        'window_days': 30,
        'totals': {'Memory Created': 2, 'Chat Message Sent': 5, 'Bad': 'x', 'Zero': 0},
        'recent': [
          {'event': 'Memory Created', 'at': '2026-11-24T10:00:00+00:00'},
          {'event': 'Chat Message Sent', 'at': '2026-11-25T10:00:00+00:00'},
          {'event': '', 'at': '2026-11-25T11:00:00+00:00'},
          {'event': 'No time', 'at': ''},
        ],
      });
      expect(result.totals.map((t) => t.event), ['Chat Message Sent', 'Memory Created']);
      expect(result.total, 7);
      expect(result.recent.map((e) => e.event), ['Chat Message Sent', 'Memory Created']);
      expect(result.windowDays, 30);
    });

    test('a server with counting off says so', () {
      expect(report({'enabled': false}).enabled, isFalse);
      expect(report(const {}).totals, isEmpty);
    });
  });

  testWidgets('shows the total, the most used actions and recent activity', (tester) async {
    await pump(
        tester,
        () async => ApiSuccess(report({
              'enabled': true,
              'window_days': 30,
              'totals': {'Chat Message Sent': 5, 'Memory Created': 2},
              'recent': [
                {'event': 'Chat Message Sent', 'at': '2026-11-25T10:00:00+00:00'}
              ],
            })));
    expect(find.text('Your usage on this server'), findsOneWidget);
    expect(find.text('7 actions in the last 30 days'), findsOneWidget);
    expect(find.text('Most used'), findsOneWidget);
    expect(find.text('Recent activity'), findsOneWidget);
    expect(find.text('Chat Message Sent'), findsNWidgets(2));
  });

  testWidgets('counting off says so', (tester) async {
    await pump(tester, () async => ApiSuccess(report({'enabled': false})));
    expect(find.text('Usage counting is turned off on this server.'), findsOneWidget);
  });

  testWidgets('nothing counted says so', (tester) async {
    await pump(tester, () async => ApiSuccess(report({'enabled': true})));
    expect(find.text('Nothing counted yet.'), findsOneWidget);
  });

  testWidgets('a failed first load offers Try Again', (tester) async {
    var calls = 0;
    await pump(tester, () async {
      calls++;
      return const ApiFailure(ApiProblem(ApiProblemKind.transport));
    });
    expect(find.text("Couldn't load your usage."), findsOneWidget);
    await tester.tap(find.byType(OmiButton).first);
    await tester.pumpAndSettle();
    expect(calls, 2);
  });
}
