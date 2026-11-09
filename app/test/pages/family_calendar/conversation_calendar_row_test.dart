// The conversation page offers its own heard events for checking, and only when there are some.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/backend/schema/structured.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/family_calendar/conversation_calendar_row.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';

import '../../helpers/summary_fixtures.dart';

Widget _app(Widget child) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: child),
    );

void main() {
  testWidgets('no events heard: no row', (tester) async {
    await tester.pumpWidget(_app(ConversationCalendarRow(conversation: summaryConversation(id: 'c1'))));
    expect(find.byKey(const Key('conversation_calendar_c1')), findsNothing);
  });

  testWidgets('events heard: the row counts them', (tester) async {
    final conversation = summaryConversation(id: 'c1');
    conversation.structured.events.addAll([
      Event('Inspection', DateTime(2026, 11, 13, 11), 60),
      Event('Movers', DateTime(2026, 11, 14, 9), 120),
    ]);
    await tester.pumpWidget(_app(ConversationCalendarRow(conversation: conversation)));
    expect(find.byKey(const Key('conversation_calendar_c1')), findsOneWidget);
    expect(find.text('2 events heard here'), findsOneWidget);
  });

  testWidgets('opened from a conversation, the page names it and shows a past event too', (tester) async {
    final past = FamilyCalendarEvent(
      conversationId: 'c1',
      eventIdx: 0,
      conversationTitle: 'Call with the landlord',
      title: 'Walkthrough',
      description: '',
      start: DateTime(2020, 1, 1, 10),
      duration: 30,
    );
    await tester.pumpWidget(_app(FamilyCalendarReviewPage(
      conversationId: 'c1',
      conversationTitle: 'Call with the landlord',
      load: () async => ApiSuccess(FamilyCalendar(
        configured: true,
        label: 'ALLGlance, dayGLANCE',
        members: const [],
        ownerId: null,
        ownerName: '',
        pending: [past],
        recent: const [],
      )),
    )));
    await tester.pumpAndSettle();
    expect(find.text('Call with the landlord'), findsOneWidget);
    expect(find.byKey(const Key('family_calendar_event_c1:0')), findsOneWidget);
  });
}
