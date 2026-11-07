// Family calendar (omi-local): the events Omi heard, waiting for the user to check the date
// and time before they go to dayGLANCE through ALLGlance. Synthetic data: three heard events,
// one already added and one declined.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';

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

final familyCalendarScenarios = <AuditScenario>[
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
];
