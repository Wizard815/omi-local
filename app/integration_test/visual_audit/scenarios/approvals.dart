// omi-local: one CalDAV account shared by everyone on the server, and nothing Omi heard reaching it
// before the user's OK. Calendar sync settings (my calendar, my task list, mode, auto-approve), the
// "Waiting for your OK" page, a day summary draft with its Approve button, the banner that says what
// waits, and the Calendar check without a family-calendar gateway. Synthetic data: Alice and Bob
// share the bundled Radicale account; yesterday left three heard tasks, two heard dates and a draft.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/pages/approvals/approvals_page.dart';
import 'package:omi/pages/day_review/day_review_banner.dart';
import 'package:omi/pages/day_review/day_review_page.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';
import 'package:omi/pages/settings/caldav_settings_page.dart';

import '../harness.dart';

const _home = 'http://127.0.0.1:5232/omi/';
final _now = DateTime.now();
final _yesterday = DateTime(_now.year, _now.month, _now.day - 1);
String _key(DateTime d) => '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
String get _date => _key(_yesterday);
String _utc(DateTime local) => local.toUtc().toIso8601String();
DateTime _at(int plusDays, int hour, [int minute = 0]) =>
    DateTime(_now.year, _now.month, _now.day + plusDays, hour, minute);

Map<String, Object?> _settings({String taskMode = 'list', String tasksHref = '${_home}omi-tasks-alice/'}) => {
      'configured': true,
      'bundled': true,
      'shared_account': true,
      'url': 'http://127.0.0.1:5232/',
      'username': 'omi',
      'has_password': true,
      'calendar_href': '${_home}omi-alice/',
      'tasks_href': tasksHref,
      'enabled': true,
      'push_tasks': true,
      'pull_tasks': true,
      'push_day_summaries': true,
      'push_conversation_events': true,
      'push_conversations': false,
      'pull_events': true,
      'task_mode': taskMode,
      'owner_label': 'Alice',
      'suggested_calendar_name': 'Omi – Alice',
      'suggested_tasks_name': 'Omi Tasks – Alice',
      'parent_task_name': "Alice's tasks",
      'last_sync_at': _utc(_at(0, 7, 42)),
      'last_error': '',
      'last_report': {
        'tasks_pushed': 3,
        'events_pushed': 2,
        'tasks_pulled': 1,
        'tasks_imported': 0,
        'events_pulled': 4
      },
    };

Map<String, Object?> _collection(String slug, String name, String component,
        {List<String> usedBy = const [], List<String> mine = const []}) =>
    {
      'href': '$_home$slug/',
      'name': name,
      'components': [component],
      'used_by': usedBy,
      'mine': mine,
    };

final _collections = {
  'ok': true,
  'error': '',
  'collections': [
    _collection('omi-alice', 'Omi – Alice', 'VEVENT', mine: ['calendar']),
    _collection('omi-tasks-alice', 'Omi Tasks – Alice', 'VTODO', mine: ['tasks']),
    _collection('omi-bob', 'Omi – Bob', 'VEVENT', usedBy: ['Bob']),
    _collection('omi-tasks-bob', 'Omi Tasks – Bob', 'VTODO', usedBy: ['Bob']),
    _collection('family', 'Family', 'VTODO', usedBy: ['Bob']),
    _collection('omi', 'Omi', 'VEVENT'),
  ],
};

Map<String, Object?> _task(String id, String text, {DateTime? due}) => {
      'id': id,
      'description': text,
      'completed': false,
      'status': 'active',
      'approval': 'pending',
      'source': 'transcript',
      'conversation_id': 'f1',
      'created_at': _utc(DateTime(_yesterday.year, _yesterday.month, _yesterday.day, 18, 5)),
      if (due != null) 'due_at': _utc(due),
    };

Map<String, Object?> _waiting({bool autoApprove = false, List<String>? keep}) {
  final tasks = [
    _task('t1', "Call Dr. Weber's office to confirm Mia's check-up", due: _at(0, 10)),
    _task('t2', 'Order the birthday cake', due: _at(2, 12)),
    _task('t3', 'Buy new soccer cleats for Leo'),
  ].where((t) => keep == null || keep.contains(t['id'])).toList();
  return {
    'tasks': tasks,
    'task_count': tasks.length,
    'event_count': 2,
    'draft_dates': [_date],
    'today': _key(_now),
    'auto_approve': autoApprove,
    'auto_approve_time': '21:00',
  };
}

const _approvalOff = {'auto_approve': false, 'time': '21:00', 'operator_disabled': false};

final approvalScenarios = <AuditScenario>[
  AuditScenario(
    id: 'caldav-settings-shared-account',
    title: 'Calendar sync: my own calendar and task list on a shared CalDAV account, auto-approve off',
    page: 'lib/pages/settings/caldav_settings_page.dart (CalDavSettingsPage)',
    state:
        'Bundled Radicale shared by Alice and Bob; Alice has her own pair; last sync sent 3 tasks and 2 events; auto-approve off',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/caldav/settings'] = _settings();
      a.server.jsonRoutes['GET /v1/caldav/collections'] = _collections;
      a.server.jsonRoutes['GET /v1/approvals/settings'] = _approvalOff;
      await a.pump(const CalDavSettingsPage());
      await a.settle();
      await a.shot('Open Settings, Calendar sync', step: 'top');
      await a.tap(find.byKey(const Key('caldav_my_tasks')));
      await a.shot('My task list: the server\'s lists, Bob\'s marked "Used by Bob", Create new… at the end',
          step: 'picker');
      await a.tap(find.byKey(const Key('caldav_collection_create')));
      await a.shot('Create new…: the name is prefilled with Alice\'s', step: 'create');
      await a.tester.tap(find.byTooltip('Close'));
      await a.settle();
      a.server.jsonRoutes['PUT /v1/caldav/settings'] = _settings(taskMode: 'parent', tasksHref: '${_home}family/');
      await a.tap(find.byKey(const Key('caldav_task_mode')));
      await a.shot('Tasks go: own list, or under one task named after her in a shared list', step: 'mode');
      await a.tap(find.byKey(const Key('caldav_mode_parent')));
      await a.shot('Parent mode in the shared Family list', step: 'parent');
      a.server.jsonRoutes['PUT /v1/approvals/settings'] = {..._approvalOff, 'auto_approve': true};
      await a.tester.ensureVisible(find.byKey(const Key('approval_auto_toggle')));
      await a.settle();
      await a.tap(find.byKey(const Key('approval_auto_toggle')));
      await a.shot('Auto-approve turned on: every day at 21:00', step: 'auto-on');
      expect(find.byKey(const Key('approval_auto_time')), findsOneWidget);
    },
  ),
  AuditScenario(
    id: 'approvals-waiting',
    title: 'Waiting for your OK: three heard tasks, two dates, yesterday\'s summary draft',
    page: 'lib/pages/approvals/approvals_page.dart (ApprovalsPage)',
    state: 'Yesterday left three extracted tasks (none on the calendar yet), two heard dates and a summary draft',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/approvals'] = _waiting();
      await a.pump(const ApprovalsPage());
      await a.settle();
      await a.shot('Open the list from the banner', step: 'top');
      a.server.jsonRoutes['POST /v1/approvals/tasks/t1/approve'] = {'ok': true, 'error': ''};
      await a.tap(find.byKey(const ValueKey('approve_task_t1')));
      await a.shot('Approve the first task: it goes to the task list and leaves this one', step: 'approved');
      a.server.jsonRoutes['POST /v1/approvals/tasks/t3/decline'] = {'ok': true, 'error': ''};
      await a.tap(find.byKey(const ValueKey('decline_task_t3')));
      await a.shot('Decline the third: an Undo toast', step: 'declined');
    },
  ),
  AuditScenario(
    id: 'day-review-draft',
    title: 'A day summary draft: nothing sent yet, Approve at the bottom, the day\'s waiting tasks',
    page: 'lib/pages/day_review/day_review_page.dart (DayReviewPage)',
    state: 'The overnight job wrote yesterday\'s draft; two of its tasks wait for approval',
    run: (a) async {
      const text = "Mia's check-up is on Thursday at three; someone calls Dr. Weber's office in the morning.\n\n"
          "Leo's practice moved to Thursday evening and he needs new cleats.\n\n"
          "Grandma's 80th is on Saturday at Luigi's; the cake is picked up at four.";
      a.server.jsonRoutes['GET /v1/users/day-reviews/$_date'] = {
        'date': _date,
        'review': {
          'date': _date,
          'status': 'draft',
          'title': 'A dentist, a practice and grandma\'s 80th',
          'summary_text': text,
          'final_text': text,
          'corrections': <Object?>[],
          'sections': <Object?>[],
          'tags': ['family'],
          'kept_conversation_ids': <Object?>[],
          'rolled_up_conversation_ids': ['f1', 'f2', 'f3'],
          'merged_groups': <Object?>[],
          'dropped_conversation_ids': <Object?>[],
          'generated_at': _utc(DateTime(_now.year, _now.month, _now.day, 3, 2)),
          'generation_count': 1,
        },
        'conversations': [
          for (final (id, h, title) in [
            ('f1', 8, "Mia's dentist appointment"),
            ('f2', 12, "Leo's soccer season"),
            ('f3', 18, "Grandma's 80th birthday"),
          ])
            {
              'id': id,
              'title': title,
              'emoji': '',
              'overview': '',
              'started_at': _utc(DateTime(_yesterday.year, _yesterday.month, _yesterday.day, h)),
              'finished_at': _utc(DateTime(_yesterday.year, _yesterday.month, _yesterday.day, h, 12)),
              'starred': false,
              'tags': ['family'],
              'rolled_up': false,
              'dropped': false,
              'held': false,
              'hold_rule': '',
              'transcript_snippet': '',
              'word_count': 120,
            }
        ],
        'processing_count': 0,
        'dropped_auto_count': 0,
        'hold_enabled': false,
      };
      a.server.jsonRoutes['GET /v1/approvals'] = _waiting(keep: ['t1', 't2']);
      await a.pump(DayReviewPage(date: _date));
      await a.settle();
      await a.shot('Open yesterday: a draft, with Approve the day at the bottom', step: 'top');
      await a.tester.ensureVisible(find.byKey(const ValueKey('approve_task_t2')));
      await a.settle();
      await a.shot('From this day: the day\'s tasks Omi heard wait here too', step: 'from-this-day');
    },
  ),
  AuditScenario(
    id: 'day-review-banner-waiting',
    title: 'Conversations banner: what waits for the user\'s OK',
    page: 'lib/pages/day_review/day_review_banner.dart (DayReviewBanner)',
    state: 'Three heard tasks, two heard dates and yesterday\'s summary draft wait',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/users/day-reviews/pending'] = {
        'today': _key(_now),
        'pending_dates': [_date],
        'draft_dates': [_date],
      };
      a.server.jsonRoutes['GET /v1/approvals'] = _waiting();
      await a.pump(const Column(mainAxisSize: MainAxisSize.min, children: [DayReviewBanner()]));
      await a.settle();
      await a.shot('The top of the conversation list', step: 'banner');
    },
  ),
  AuditScenario(
    id: 'calendar-check-caldav-only',
    title: 'Calendar check without a family calendar: Approve adds to my CalDAV calendar, and can be taken off',
    page: 'lib/pages/family_calendar/family_calendar_review_page.dart (FamilyCalendarReviewPage)',
    state: 'No ALLGlance gateway; CalDAV sync on; two heard dates, one already approved',
    run: (a) async {
      Map<String, Object?> event(String cid, String title, DateTime start, {bool approved = false}) => {
            'conversation_id': cid,
            'event_idx': 0,
            'conversation_title': title,
            'title': title,
            'description': '',
            'start': _utc(start),
            'duration': 45,
            'heard_at': _utc(DateTime(_yesterday.year, _yesterday.month, _yesterday.day, 12)),
            'created': false,
            'calendar': null,
            'external_id': null,
            'declined': false,
            'approved': approved,
          };
      a.server.jsonRoutes['GET /v1/family-calendar'] = {
        'configured': false,
        'caldav': true,
        'label': '',
        'members': <Object?>[],
        'owner_name': '',
        'pending': [event('e1', 'Mia dentist check-up', _at(2, 15))],
        'recent': [event('e2', 'Leo soccer practice', _at(3, 17, 30), approved: true)],
      };
      await a.pump(const FamilyCalendarReviewPage());
      await a.settle();
      await a.shot('Open the Calendar check (no dayGLANCE here)', step: 'top');
    },
  ),
];
