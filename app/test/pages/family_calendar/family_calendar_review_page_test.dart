// The ask-first contract on the review page: nothing is sent until "Add to calendar" is
// tapped, the user's edits are what gets sent, and a refusal keeps the card.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';

final _start = DateTime(2026, 11, 13, 11, 0);

FamilyCalendarEvent _inspection() => FamilyCalendarEvent(
      conversationId: 'c10',
      eventIdx: 0,
      conversationTitle: 'Call with the landlord',
      title: 'Inspecton friday',
      description: '',
      start: _start,
      duration: 60,
      heardAt: DateTime(2026, 11, 7, 14, 0),
    );

class _Fake {
  List<FamilyCalendarEvent> pending = [_inspection()];
  final approved = <Map<String, Object?>>[];
  final declined = <String>[];
  bool refuse = false;
  int loads = 0;
  String? ownerId;
  final ownerCalls = <String?>[];
  static const members = [
    FamilyCalendarMember(id: 'u-sam', name: 'Sam'),
    FamilyCalendarMember(id: 'u-dana', name: 'Dana'),
  ];

  FamilyCalendar _calendar() => FamilyCalendar(
        configured: true,
        label: 'Family calendar (ALLGlance)',
        members: members,
        ownerId: ownerId,
        ownerName: ownerId == null ? '' : members.firstWhere((m) => m.id == ownerId).name,
        pending: pending,
        recent: const [],
      );

  Future<ApiResult<FamilyCalendar>> load() async {
    loads++;
    return ApiSuccess(_calendar());
  }

  Future<ApiResult<FamilyCalendar>> setOwner(String? id) async {
    ownerCalls.add(id);
    ownerId = id;
    return ApiSuccess(_calendar());
  }

  Future<ApiResult<FamilyCalendarOutcome>> approve(FamilyCalendarEvent event,
      {String? title, String? description, DateTime? start, int? duration}) async {
    approved.add({'key': event.key, 'title': title, 'description': description, 'start': start, 'duration': duration});
    if (refuse) return const ApiSuccess(FamilyCalendarOutcome(ok: false, error: 'dayGLANCE is off'));
    pending = [];
    return const ApiSuccess(FamilyCalendarOutcome(ok: true));
  }

  Future<ApiResult<FamilyCalendarOutcome>> decline(FamilyCalendarEvent event) async {
    declined.add(event.key);
    pending = [];
    return const ApiSuccess(FamilyCalendarOutcome(ok: true));
  }
}

Future<void> _open(WidgetTester tester, _Fake fake) async {
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: FamilyCalendarReviewPage(
        load: fake.load, approve: fake.approve, decline: fake.decline, setOwner: fake.setOwner),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('listing sends nothing; approving sends the edited title and length', (tester) async {
    final fake = _Fake();
    await _open(tester, fake);
    expect(find.byKey(const Key('family_calendar_event_c10:0')), findsOneWidget);
    expect(fake.approved, isEmpty);
    expect(fake.declined, isEmpty);

    await tester.enterText(find.byKey(const Key('family_calendar_title_c10:0')), 'Inspection');
    await tester.enterText(find.byKey(const Key('family_calendar_description_c10:0')), 'Landlord, bring the keys');
    await tester.tap(find.byKey(const Key('family_calendar_duration_c10:0'))); // 60 -> 90
    await tester.pumpAndSettle();
    expect(find.text('90 min'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('family_calendar_approve_c10:0')));
    await tester.tap(find.byKey(const Key('family_calendar_approve_c10:0')));
    await tester.pumpAndSettle();

    expect(fake.approved.single['key'], 'c10:0');
    expect(fake.approved.single['title'], 'Inspection');
    expect(fake.approved.single['description'], 'Landlord, bring the keys');
    expect(fake.approved.single['duration'], 90);
    expect(fake.approved.single['start'], _start, reason: 'untouched date and time are sent as heard');
    expect(find.byKey(const Key('family_calendar_event_c10:0')), findsNothing, reason: 'reloaded after the add');
  });

  testWidgets('a refusal keeps the card and shows the reason', (tester) async {
    final fake = _Fake()..refuse = true;
    await _open(tester, fake);
    await tester.ensureVisible(find.byKey(const Key('family_calendar_approve_c10:0')));
    await tester.tap(find.byKey(const Key('family_calendar_approve_c10:0')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('family_calendar_event_c10:0')), findsOneWidget);
    expect(find.textContaining('dayGLANCE is off'), findsOneWidget);
  });

  testWidgets('declining never calls approve and removes the card', (tester) async {
    final fake = _Fake();
    await _open(tester, fake);
    await tester.ensureVisible(find.byKey(const Key('family_calendar_decline_c10:0')));
    await tester.tap(find.byKey(const Key('family_calendar_decline_c10:0')));
    await tester.pumpAndSettle();
    expect(fake.declined, ['c10:0']);
    expect(fake.approved, isEmpty);
    expect(find.byKey(const Key('family_calendar_event_c10:0')), findsNothing);
  });

  testWidgets('picking a household member binds the added events to them', (tester) async {
    final fake = _Fake();
    await _open(tester, fake);
    expect(find.text('Adding as: Whole family'), findsOneWidget);
    await tester.tap(find.byKey(const Key('family_calendar_owner')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('family_calendar_owner_u-sam')));
    await tester.pumpAndSettle();
    expect(fake.ownerCalls, ['u-sam']);
    expect(find.text('Adding as: Sam'), findsOneWidget);
    expect(fake.approved, isEmpty, reason: 'choosing a member adds nothing by itself');
  });
}
