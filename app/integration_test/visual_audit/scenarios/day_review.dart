// Day review (omi-local): the sorting view of a day of held clips, and the bundled review with its
// sections, tags and favorite-out. Synthetic data: one day, twelve clips, seven of them small.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/pages/day_review/day_review_page.dart';

import '../harness.dart';

const _page = 'lib/pages/day_review/day_review_page.dart (DayReviewPage)';

final _day = DateTime.now().subtract(const Duration(days: 1));
String get _date => '${_day.year}-${_day.month.toString().padLeft(2, '0')}-${_day.day.toString().padLeft(2, '0')}';
String _at(int hour, int minute, [int plusMinutes = 0]) =>
    DateTime(_day.year, _day.month, _day.day, hour, minute + plusMinutes).toUtc().toIso8601String();

Map<String, Object?> _clip(String id, int h, int m, String title,
        {bool small = false,
        bool starred = false,
        bool rolled = false,
        List<String> tags = const [],
        String overview = ''}) =>
    {
      'id': id,
      'title': title,
      'emoji': '',
      'overview': overview,
      'started_at': _at(h, m),
      'finished_at': _at(h, m, small ? 1 : 14),
      'starred': starred,
      'tags': tags,
      'rolled_up': rolled,
      'dropped': false,
      'held': !rolled && !starred,
      'hold_rule': 'hold',
      'transcript_snippet': small
          ? 'Flat white please. Thanks.'
          : 'Where are we on this? I looked at it this morning and the main problem is clear now.',
      'word_count': small ? 6 : 84,
    };

List<Map<String, Object?>> _clips({required bool bundled}) => [
      _clip('c1', 7, 48, 'Morning: coffee order',
          small: true, rolled: bundled, tags: bundled ? ['small', 'passing'] : const []),
      _clip('c2', 8, 15, 'Standup with Dana and Luis',
          rolled: bundled,
          tags: bundled ? ['standup', 'dana', 'luis'] : const [],
          overview: bundled ? 'Dana finished the ingest retry path; Luis is on the CalDAV pull.' : ''),
      _clip('c3', 8, 31, 'Hallway', small: true, rolled: bundled, tags: bundled ? ['small', 'passing'] : const []),
      _clip('c4', 9, 02, 'Build break: google-services.json',
          rolled: bundled, tags: bundled ? ['build', 'android'] : const []),
      _clip('c5', 9, 40, 'Delivery at the door',
          small: true, rolled: bundled, tags: bundled ? ['small', 'passing'] : const []),
      _clip('c6', 10, 05, 'Design review: widgets',
          rolled: bundled, tags: bundled ? ['design', 'widgets', 'priya'] : const []),
      _clip('c7', 10, 44, 'Reminder to self',
          small: true, rolled: bundled, tags: bundled ? ['small', 'passing'] : const []),
      _clip('c8', 12, 20, 'Lunch with Dana',
          starred: true,
          tags: bundled ? ['lunch', 'dana', 'move'] : const [],
          overview: bundled ? 'The nightly failed again; Dana will push from her Mac. Movers Saturday.' : ''),
      _clip('c9', 13, 05, 'Elevator', small: true, rolled: bundled, tags: bundled ? ['small', 'passing'] : const []),
      _clip('c10', 14, 00, 'Call with the landlord',
          rolled: bundled, tags: bundled ? ['landlord', 'inspection'] : const []),
      _clip('c11', 15, 30, 'Snack run', small: true, rolled: bundled, tags: bundled ? ['small', 'passing'] : const []),
      _clip('c12', 17, 40, 'Leaving', small: true, rolled: bundled, tags: bundled ? ['small', 'passing'] : const []),
    ];

Map<String, Object?> _section(String id, String title, String text, List<String> ids, List<String> tags,
        {bool favorited = false}) =>
    {'id': id, 'title': title, 'text': text, 'tags': tags, 'conversation_ids': ids, 'favorited': favorited};

final _sections = [
  _section('s1', 'Small moments', 'Between things you had a coffee order at 07:48 and a hallway hello at 08:31.',
      ['c1', 'c3'], ['small', 'passing']),
  _section(
      's2',
      'Standup with Dana and Luis',
      'At 08:15 you went through the standup. Dana finished the ingest retry path and takes the deadline test today; Luis is still on the CalDAV pull, where TZID handling is the tricky bit. You took the Android build break and agreed to look at the widget tap on Android 14 after it. Demo Thursday, dry run Wednesday.',
      ['c2'],
      ['standup', 'dana', 'luis']),
  _section(
      's3',
      'Build break: google-services.json',
      'At 09:02 you found the dev flavor fails on a fresh clone because the Google Services plugin wants a file the repo never has. Since Firebase is configured from Dart, you decided to remove the plugin and drop Twilio with it.',
      ['c4'],
      ['build', 'android']),
  _section(
      's4',
      'Small moments',
      'A delivery at 09:40 and a note to yourself at 10:44 to call the dentist before five.',
      ['c5', 'c7'],
      ['small', 'passing']),
  _section(
      's5',
      'Design review: widgets',
      'At 10:05 Priya pushed back on five widgets for a first cut. You kept Mute and the transcript, made the lists plain rows, and agreed taps open the app through its own links so there is nothing new to maintain.',
      ['c6'],
      ['design', 'widgets', 'priya'],
      favorited: true),
  _section('s6', 'Small moments', 'An elevator ride at 13:05.', ['c9'], ['small', 'passing']),
  _section(
      's7',
      'Call with the landlord',
      'At 14:00 the landlord set the inspection for Friday; you moved it from ten to eleven to clear the dry run. The deposit comes back within fourteen days after.',
      ['c10'],
      ['landlord', 'inspection']),
  _section('s8', 'Small moments', 'A snack run at 15:30 and heading out at 17:40, reminding Dana about the dry run.',
      ['c11', 'c12'], ['small', 'passing']),
];

Map<String, Object?> _review() {
  final text = _sections.map((s) => s['text']).join('\n\n');
  return {
    'date': _date,
    'status': 'finalized',
    'title': 'A build fixed, a flat found, twelve small moments',
    'summary_text': text,
    'final_text': text,
    'corrections': <Object?>[],
    'sections': _sections,
    'tags': [
      'android',
      'build',
      'dana',
      'design',
      'inspection',
      'landlord',
      'luis',
      'passing',
      'priya',
      'small',
      'standup',
      'widgets'
    ],
    'kept_conversation_ids': ['c8', 'c6'],
    'rolled_up_conversation_ids': ['c1', 'c2', 'c3', 'c4', 'c5', 'c7', 'c9', 'c10', 'c11', 'c12'],
    'merged_groups': <Object?>[],
    'dropped_conversation_ids': <Object?>[],
    'generated_at': DateTime.now().toUtc().toIso8601String(),
    'generation_count': 1,
    'time_zone': 'UTC',
  };
}

Map<String, Object?> _dayDoc({required bool bundled}) => {
      'date': _date,
      'review': bundled ? _review() : null,
      'conversations': _clips(bundled: bundled),
      'processing_count': 0,
      'dropped_auto_count': 0,
      'hold_enabled': true,
    };

final dayReviewScenarios = <AuditScenario>[
  AuditScenario(
    id: 'day-review-held',
    title: 'Day review before bundling: twelve held clips, one starred',
    page: _page,
    state: 'Hold mode: the day\'s clips wait for the review; Lunch with Dana is starred; nothing bundled yet',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/users/day-reviews/$_date'] = _dayDoc(bundled: false);
      await a.pump(DayReviewPage(date: _date));
      await a.settle();
      await a.shot('Open the day before it is bundled');
    },
  ),
  AuditScenario(
    id: 'day-review-bundled',
    title: 'Day review after bundling: eight sections, tags, one section favorited out',
    page: _page,
    state:
        'Bundled: five substantive sections and three "Small moments" groups; "Design review" favorited back out; Lunch kept',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/users/day-reviews/$_date'] = _dayDoc(bundled: true);
      // Favoriting "Standup" answers with that section favorited and its clip kept.
      final favorited = _review();
      final sections = (favorited['sections'] as List).cast<Map<String, Object?>>();
      sections[1] = {...sections[1], 'favorited': true};
      favorited['sections'] = sections;
      favorited['kept_conversation_ids'] = ['c8', 'c6', 'c2'];
      favorited['rolled_up_conversation_ids'] = ['c1', 'c3', 'c4', 'c5', 'c7', 'c9', 'c10', 'c11', 'c12'];
      a.server.jsonRoutes['POST /v1/users/day-reviews/$_date/sections/s2/favorite'] = favorited;
      await a.pump(DayReviewPage(date: _date));
      await a.settle();
      await a.shot('Open the bundled day', step: 'top');
      await a.tap(find.byKey(const Key('day_review_section_clips_s1')));
      await a.shot('Unfold the clips behind the first "Small moments" section', step: 'clips-open');
      await a.tester.ensureVisible(find.byKey(const Key('day_review_section_favorite_s2')));
      await a.settle();
      await a.tap(find.byKey(const Key('day_review_section_favorite_s2')));
      await a.shot('Favorite the standup section: it is kept as its own conversation', step: 'favorited');
      expect(find.byKey(const Key('day_review_section_favorite_s2')), findsOneWidget);
      await a.tester.drag(find.byType(ListView).first, const Offset(0, -1200));
      await a.settle();
      await a.shot('Scroll to the later sections, the tags and the lists', step: 'scrolled');
    },
  ),
];
