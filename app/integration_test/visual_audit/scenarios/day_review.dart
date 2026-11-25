// Day review (omi-local): the sorting view of a day of held clips, and the bundled review with its
// sections, tags and favorite-out. Synthetic data: one day, twelve clips, seven of them small.
// The family-day scenarios walk one household's day end to end: sort (star, merge, drop), bundle,
// edit the summary, then the day's tasks and the calendar check reached from "From this day".
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
      // Scroll it into view: the list builds a section only near the screen.
      await a.tester.scrollUntilVisible(find.byKey(const Key('day_review_section_favorite_s2')), 300,
          scrollable: find.byType(Scrollable).first);
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

// ------------------------------------------------------------------------------------------------
// A family day: eight clips, two of them about the same swim meet, one call that covered two
// topics (the pediatrician and Leo's soccer form), tasks and three heard dates.

Map<String, Object?> _famClip(String id, int h, int m, int minutes, String title, String snippet,
        {bool starred = false, bool rolled = false, bool held = true, String overview = ''}) =>
    {
      'id': id,
      'title': title,
      'emoji': '',
      'overview': overview,
      'started_at': _at(h, m),
      'finished_at': _at(h, m, minutes),
      'starred': starred,
      'tags': <String>[],
      'rolled_up': rolled,
      'dropped': false,
      'held': held && !starred && !rolled,
      'hold_rule': minutes <= 2 ? 'model_withheld' : 'hold',
      'transcript_snippet': snippet,
      'word_count': minutes * 40,
    };

List<Map<String, Object?>> _famClips({bool bundled = false}) => [
      _famClip('f1', 7, 40, 2, 'School drop-off', 'Backpack, lunch, water bottle. Love you, have a good day.',
          rolled: bundled),
      _famClip('f2', 8, 10, 12, 'Call with the pediatrician',
          'Thursday at three thirty works. And while I have you, does Leo need the physical for soccer?',
          rolled: bundled),
      _famClip('f3', 9, 30, 6, 'Grocery run', 'Oat milk, apples, the good bread, and candles for Saturday.',
          rolled: bundled),
      _famClip('f4', 12, 15, 34, 'Lunch with Grandma',
          'Sunday dinner at five at ours. Her knee is better but she should not drive at night.',
          starred: true, overview: 'Sunday dinner at five; Sam picks her up because of the night drive.'),
      _famClip('f5', 15, 45, 4, 'Swim pickup', 'Coach said Mia did great today. The meet is on the 28th.',
          rolled: bundled),
      _famClip('f6', 16, 5, 9, 'Swim coach about the meet',
          'Warm-up is at seven, so be there by six forty-five. Bring the team cap.',
          rolled: bundled),
      _famClip('f7', 18, 30, 22, 'Dinner: weekend plans',
          'Soccer Saturday at nine at Lincoln field, then the birthday party at two. Dentist reminder for Mia.',
          rolled: bundled),
      _famClip('f8', 20, 10, 1, 'Bedtime', 'Goodnight. Lights off.', rolled: bundled),
    ];

final _famSections = [
  _section(
      'fs1',
      'Morning',
      'You dropped the kids at school at 07:40 and did a grocery run at 09:30: oat milk, apples, bread and candles for Saturday.',
      ['f1', 'f3'],
      ['school', 'groceries']),
  _section(
      'fs2',
      'Call with the pediatrician',
      'At 08:10 you booked Mia\'s check-up for Thursday at 3:30. On the same call you asked whether Leo needs a physical for soccer; he does, and the form has to be in before the first game.',
      ['f2'],
      ['mia', 'leo', 'doctor', 'soccer']),
  _section(
      'fs3',
      'Swim meet on the 28th',
      'At pickup the coach said Mia did great. The meet is on the 28th: warm-up at seven, so be there by 6:45 with the team cap.',
      ['f5', 'f6'],
      ['mia', 'swim']),
  _section(
      'fs4',
      'Dinner: weekend plans',
      'Over dinner you planned the weekend: soccer on Saturday at nine at Lincoln field, then a birthday party at two. Mia is due at the dentist.',
      ['f7'],
      ['weekend', 'soccer', 'party']),
];

String get _famText => _famSections.map((s) => s['text']).join('\n\n');

Map<String, Object?> _famReview({List<Map<String, Object?>> corrections = const []}) => {
      'date': _date,
      'status': 'finalized',
      'title': 'A check-up booked, a swim meet set, a full weekend',
      'summary_text': _famText,
      'final_text': _famText,
      'corrections': corrections,
      'sections': _famSections,
      'tags': ['doctor', 'leo', 'mia', 'party', 'school', 'soccer', 'swim', 'weekend'],
      'kept_conversation_ids': ['f4'],
      'rolled_up_conversation_ids': ['f1', 'f2', 'f3', 'f5', 'f6', 'f7'],
      'merged_groups': <Object?>[],
      'dropped_conversation_ids': ['f8'],
      'generated_at': DateTime.now().toUtc().toIso8601String(),
      'generation_count': 1,
      'time_zone': 'UTC',
    };

Map<String, Object?> _famDay({Map<String, Object?>? review}) => {
      'date': _date,
      'review': review,
      'conversations': _famClips(bundled: review != null),
      'processing_count': 0,
      'dropped_auto_count': 1,
      'hold_enabled': true,
    };

int _offsetOf(String quote) => _famText.indexOf(quote);

Map<String, Object?> _highlight(String id, String quote, String text) => {
      'id': id,
      'type': 'highlight',
      'quote': quote,
      'start': _offsetOf(quote),
      'end': _offsetOf(quote) + quote.length,
      'text': text,
      'created_at': DateTime.now().toUtc().toIso8601String(),
    };

Map<String, Object?> _paragraph(String id, String text, int after) => {
      'id': id,
      'type': 'paragraph',
      'text': text,
      'after_paragraph': after,
      'created_at': DateTime.now().toUtc().toIso8601String(),
    };

Map<String, Object?> _famTask(String id, String description, {int dueInDays = 1, bool completed = false}) {
  final due = DateTime(_day.year, _day.month, _day.day + dueInDays, 17);
  return {
    'id': id,
    'description': description,
    'completed': completed,
    'created_at': DateTime(_day.year, _day.month, _day.day, 18, 40).toUtc().toIso8601String(),
    'updated_at': DateTime(_day.year, _day.month, _day.day, 18, 40).toUtc().toIso8601String(),
    'due_at': due.toUtc().toIso8601String(),
    'conversation_id': 'f7',
  };
}

final dayReviewFamilyScenarios = <AuditScenario>[
  AuditScenario(
    id: 'day-review-family-sort',
    title: 'Family day before review: star, merge two swim clips, drop bedtime, then bundle',
    page: _page,
    state: 'Hold mode: eight clips of a family day, Lunch with Grandma starred; nothing summarized yet',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/users/day-reviews/$_date'] = _famDay();
      await a.pump(DayReviewPage(date: _date));
      await a.settle();
      await a.shot('Open the day: eight clips waiting, Grandma starred', step: 'top');
      final checks = find.byType(Checkbox);
      await a.tester.ensureVisible(checks.at(3));
      await a.tap(checks.at(3)); // Swim pickup
      await a.tap(checks.at(4)); // Swim coach
      await a.shot('Tick the two swim clips: the merge bar appears', step: 'ticked');
      await a.tap(find.text('Merge 2 clips into one'));
      await a.shot('Merge them: one "Merged into one conversation" group with Ungroup', step: 'merged');
      await a.tester.drag(find.byType(ListView).first, const Offset(0, -2000));
      await a.settle();
      await a.tap(find.byTooltip('Drop (noise)').last); // Bedtime, the last clip
      await a.shot('Drop bedtime: the bottom button counts keep, merge, drop and summarize', step: 'sorted');
      await a.tester.drag(find.byType(ListView).first, const Offset(0, 3000));
      await a.settle();
      await a.tap(find.byKey(const Key('day_review_bundle')));
      await a.shot('Bundle the day: it now asks first and uses these choices', step: 'bundle-confirm');
      expect(find.text('Build the Day Summary?'), findsOneWidget);
    },
  ),
  AuditScenario(
    id: 'day-review-family-summary',
    title: 'Family day summary: edit, correct, remove with Undo, then the day\'s tasks and dates',
    page: _page,
    state:
        'Bundled family day: four sections, Grandma kept, bedtime dropped; one pending correction and one added paragraph',
    run: (a) async {
      final corrections = [
        _highlight('k1', 'Thursday at 3:30', 'It is 3:45, not 3:30'),
        _paragraph('k2', 'Grandma is coming Sunday at five; Sam picks her up.', 1),
      ];
      a.server.jsonRoutes['GET /v1/users/day-reviews/$_date'] = _famDay(review: _famReview(corrections: corrections));
      await a.pump(DayReviewPage(date: _date));
      await a.settle();
      await a.shot('Open the bundled day', step: 'top');
      await a.tester.drag(find.byType(ListView).first, const Offset(0, -500));
      await a.settle();
      await a.shot('Scroll: the correction is highlighted, the added paragraph sits under its section',
          step: 'corrections');
      await a.tap(find.byTooltip('Add a paragraph after this section').first);
      await a.enterText(find.byKey(const Key('day_review_text_field')), 'Leo\'s soccer form is in the blue folder.');
      await a.shot('Tap + : an editing sheet with Save and Cancel', step: 'add-sheet');
      await a.tap(find.byTooltip('Close'));
      await a.shot('Close with text typed: Discard Changes? first', step: 'add-discard');
      await a.tap(find.text('Keep Editing'));
      a.server.jsonRoutes['POST /v1/users/day-reviews/$_date/corrections'] = _famReview(corrections: [
        ...corrections,
        _paragraph('k3', 'Leo\'s soccer form is in the blue folder.', 1),
      ]);
      await a.tap(find.byKey(const Key('day_review_text_save')));
      await a.shot('Save: the paragraph waits under its section; Apply corrections (3)', step: 'added');
      final delete = find.byKey(const Key('day_review_correction_delete_k1'));
      await a.tester.scrollUntilVisible(delete, 300, scrollable: find.byType(Scrollable).first);
      await a.settle();
      a.server.jsonRoutes['DELETE /v1/users/day-reviews/$_date/corrections/k1'] = _famReview(corrections: [
        corrections[1],
        _paragraph('k3', 'Leo\'s soccer form is in the blue folder.', 1),
      ]);
      await a.tap(delete);
      await a.shot('Remove a correction: an Undo toast', step: 'removed-undo');
      expect(find.text('Correction removed'), findsOneWidget);
      final tasks = find.byKey(const Key('day_review_tasks'));
      await a.tester.scrollUntilVisible(tasks, 300, scrollable: find.byType(Scrollable).first);
      await a.tester.pump(const Duration(seconds: 6));
      await a.settle();
      await a.shot('From this day: the day\'s tasks and the calendar check', step: 'from-this-day');
      a.server.actionItems
        ..clear()
        ..addAll([
          _famTask('t1', 'Send Leo\'s soccer physical form to the coach', dueInDays: 3),
          _famTask('t2', 'Buy candles and a gift for Saturday\'s party', dueInDays: 2),
          _famTask('t3', 'Book Mia\'s dentist appointment', dueInDays: 5),
          _famTask('t4', 'Pack the team cap for the swim meet', dueInDays: 12),
          _famTask('t5', 'Pick up Grandma Sunday at 4:30', dueInDays: 4),
        ]);
      await a.tap(tasks);
      await a.settle();
      await a.shot('Tap Tasks: the tasks made that day, each editable', step: 'tasks');
    },
  ),
];
