// Family calendar (omi-local): the events Omi heard, waiting for the user to check the date
// and time before they go to dayGLANCE through ALLGlance. Synthetic data: three heard events,
// one already added and one declined.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:provider/provider.dart';

import 'package:omi/backend/schema/schema.dart';
import 'package:omi/pages/conversation_detail/conversation_detail_provider.dart';
import 'package:omi/pages/conversation_detail/page.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';
import 'package:omi/providers/conversation_provider.dart';
import 'package:omi/widgets/conversation_bottom_bar.dart' show ConversationTab;

import '../harness.dart';

const _page = 'lib/pages/family_calendar/family_calendar_review_page.dart (FamilyCalendarReviewPage)';

final _now = DateTime.now();
String _utc(DateTime local) => local.toUtc().toIso8601String();
DateTime _day(int plusDays, int hour, int minute) => DateTime(_now.year, _now.month, _now.day + plusDays, hour, minute);

Map<String, Object?> _event(String cid, int idx, String convo, String title, DateTime start,
        {int duration = 30, String description = '', bool created = false, bool declined = false}) =>
    {
      'conversation_id': cid,
      'event_idx': idx,
      'conversation_title': convo,
      'title': title,
      'description': description,
      'start': _utc(start),
      'duration': duration,
      'heard_at': _utc(_day(0, 9, 15)),
      'created': created,
      'calendar': created ? 'allglance' : null,
      'external_id': created ? 'dayglance:dg7' : null,
      'declined': declined,
    };

Map<String, Object?> _calendar({bool configured = true, bool empty = false, String? owner}) => {
      'configured': configured,
      'label': configured ? 'Family calendar (ALLGlance)' : '',
      'members': configured
          ? [
              {'id': 'u-sam', 'name': 'Sam'},
              {'id': 'u-dana', 'name': 'Dana'},
            ]
          : <Object?>[],
      'owner_id': owner,
      'owner_name': owner == 'u-sam' ? 'Sam' : (owner == 'u-dana' ? 'Dana' : ''),
      'pending': empty
          ? <Object?>[]
          : [
              _event('c10', 0, 'Call with the landlord', 'Inspection', _day(2, 11, 0),
                  duration: 60, description: 'Moved from ten to eleven to clear the dry run.'),
              _event('c2', 0, 'Standup with Dana and Luis', 'Release planning', _day(1, 14, 30), duration: 45),
              _event('c12', 1, 'Heading out', 'Dinner at the Nguyens', _day(5, 18, 30), duration: 120),
            ],
      'recent': [
        _event('c6', 0, 'Design review: widgets', 'Dentist', _day(-1, 8, 30), created: true),
        _event('c3', 0, 'Hallway', 'Something on the 3rd', _day(3, 15, 0), declined: true),
      ],
    };

/// The family day's heard dates (see day_review.dart): one to fix and add, one to decline and
/// take back.
Map<String, Object?> _familyDay({String stage = 'open'}) {
  final checkup = _event('f2', 0, 'Call with the pediatrician', 'Mia check-up', _day(3, 15, 30),
      duration: 30, description: 'Dr. Patel, bring the vaccination card.');
  final soccer = _event('f7', 0, 'Dinner: weekend plans', 'Soccer at Lincoln field', _day(5, 9, 0), duration: 90);
  final party = _event('f7', 1, 'Dinner: weekend plans', 'Birthday party', _day(5, 14, 0), duration: 120);
  return {
    ..._calendar(),
    'pending': switch (stage) {
      'added' => [soccer, party],
      'declined' => [soccer],
      _ => [checkup, soccer, party],
    },
    'recent': switch (stage) {
      'added' => [
          {...checkup, 'title': 'Mia check-up (3:45)', 'start': _utc(_day(3, 15, 45)), 'created': true},
        ],
      'declined' => [
          {...checkup, 'title': 'Mia check-up (3:45)', 'start': _utc(_day(3, 15, 45)), 'created': true},
          {...party, 'declined': true},
        ],
      _ => <Object?>[],
    },
  };
}

final familyCalendarScenarios = <AuditScenario>[
  AuditScenario(
    id: 'family-calendar-family-day',
    title: 'Family day dates: fix and add one, decline one with Undo, Bring Back later',
    page: _page,
    state: 'Three dates heard on a family day (check-up, soccer, party); the check-up time is wrong',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/family-calendar'] = _familyDay();
      await a.pump(const FamilyCalendarReviewPage());
      await a.settle();
      await a.shot('Three heard dates to check', step: 'top');
      await a.enterText(find.byKey(const Key('family_calendar_title_f2:0')), 'Mia check-up (3:45)');
      await a.shot('Fix the title; the card is marked Edited (the time is fixed with the time chip)', step: 'edited');
      a.server.jsonRoutes['POST /v1/family-calendar/events'] = {'ok': true};
      a.server.jsonRoutes['GET /v1/family-calendar'] = _familyDay(stage: 'added');
      await a.tester.ensureVisible(find.byKey(const Key('family_calendar_approve_f2:0')));
      await a.tap(find.byKey(const Key('family_calendar_approve_f2:0')));
      await a.shot('Add to calendar: confirmed, it moves to Reviewed lately', step: 'added');
      a.server.jsonRoutes['POST /v1/family-calendar/events/decline'] = {'ok': true};
      a.server.jsonRoutes['GET /v1/family-calendar'] = _familyDay(stage: 'declined');
      await a.tester.ensureVisible(find.byKey(const Key('family_calendar_decline_f7:1')));
      await a.tap(find.byKey(const Key('family_calendar_decline_f7:1')));
      await a.shot('Not this on the party: an Undo toast, and Bring Back on its reviewed row', step: 'declined');
      expect(find.byKey(const Key('family_calendar_restore_f7:1')), findsOneWidget);
    },
  ),
  AuditScenario(
    id: 'family-calendar-review',
    title: 'Family calendar: three heard events to check, two reviewed lately',
    page: _page,
    state:
        'Three pending cards with editable title, note, date, time and length; "Adding as" member chip; one event already added, one declined',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/family-calendar'] = _calendar();
      a.server.jsonRoutes['PUT /v1/family-calendar/owner'] = _calendar(owner: 'u-sam');
      a.server.jsonRoutes['POST /v1/family-calendar/events/decline'] = {
        'ok': true,
        'event': _event('c2', 0, 'Standup with Dana and Luis', 'Release planning', _day(1, 14, 30), declined: true),
      };
      await a.pump(const FamilyCalendarReviewPage());
      await a.settle();
      await a.shot('Open the review', step: 'top');
      await a.tap(find.byKey(const Key('family_calendar_owner')));
      await a.shot('Tap "Adding as": the household picker', step: 'members');
      await a.tap(find.byKey(const Key('family_calendar_owner_u-sam')));
      await a.shot('Sam picked: events will be added as Sam', step: 'owner');
      expect(find.text('Sam'), findsOneWidget);
      await a.tap(find.byKey(const Key('family_calendar_duration_c10:0')));
      await a.shot('Tap the length chip: 60 min becomes 90 min and the card says it was edited', step: 'edited');
      expect(find.text('90 min'), findsOneWidget);
      a.server.jsonRoutes['GET /v1/family-calendar'] = _calendar(empty: true);
      await a.tap(find.byKey(const Key('family_calendar_decline_c2:0')));
      await a.shot('Decline the standup one: it leaves the list', step: 'declined');
    },
  ),
  AuditScenario(
    id: 'family-calendar-unconfigured',
    title: 'Family calendar without an ALLGlance gateway',
    page: _page,
    state: 'Empty state explaining how to wire the gateway',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/family-calendar'] = _calendar(configured: false, empty: true);
      await a.pump(const FamilyCalendarReviewPage());
      await a.settle();
      await a.shot('No family calendar set', step: 'top');
    },
  ),
  AuditScenario(
    id: 'family-calendar-in-conversation',
    title: 'Calendar on a conversation: the events heard there, checked from the summary',
    page: 'lib/pages/family_calendar/conversation_calendar_row.dart (ConversationCalendarRow)',
    state: 'A conversation with two heard events shows a Calendar row; tapping it opens the check for just those',
    run: (a) async {
      final started = _day(0, 9, 15);
      final conversation = ServerConversation(
        id: 'c10',
        createdAt: started,
        startedAt: started,
        finishedAt: started.add(const Duration(minutes: 12)),
        status: ConversationStatus.completed,
        structured: Structured(
          'Call with the landlord',
          '## Inspection\n- Moved from ten to eleven on the 13th.\n\n## Movers\n- Booked for Saturday morning.',
          emoji: '\u{1F3E0}',
        ),
      );
      conversation.structured.events.addAll([
        Event('Inspection', _day(2, 11, 0), 60),
        Event('Movers', _day(4, 9, 0), 120),
      ]);
      a.server.jsonRoutes['GET /v1/family-calendar'] = {
        ..._calendar(),
        'pending': [
          _event('c10', 0, 'Call with the landlord', 'Inspection', _day(2, 11, 0),
              duration: 60, description: 'Moved from ten to eleven to clear the dry run.'),
          _event('c10', 1, 'Call with the landlord', 'Movers', _day(4, 9, 0), duration: 120),
        ],
        'recent': <Object?>[],
      };
      await a.pump(
        ConversationDetailPage(conversation: conversation, initialTab: ConversationTab.summary),
        providers: [
          ChangeNotifierProvider<ConversationProvider>.value(
            value: ConversationProvider(isSignedIn: () => true)..conversations = [conversation],
          ),
          ChangeNotifierProvider(
            create: (_) => ConversationDetailProvider()..selectedDate = conversationLocalDayKey(conversation.createdAt),
          ),
        ],
      );
      await a.settle();
      final row = find.byKey(const Key('conversation_calendar_c10'));
      await a.tester.scrollUntilVisible(row, 200, scrollable: find.byType(Scrollable).first);
      await a.settle();
      await a.shot('The summary: a Calendar row with the two events heard here', step: 'row');
      await a.tap(row);
      await a.settle();
      await a.shot('Tap it: the check for just this conversation, under its title', step: 'check');
      expect(find.byKey(const Key('family_calendar_event_c10:1')), findsOneWidget);
    },
  ),
];
