import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/backend/schema/schema.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/services/android_home_widgets.dart';
import 'package:omi/ui/format/omi_date_format.dart';
import 'package:omi/utils/enums.dart';

/// What the Android home-screen widgets are given (OmiWidgetData.kt reads it).
void main() {
  setUpAll(() => initializeDateFormatting());
  final en = lookupAppLocalizations(const Locale('en'));
  late final dates = OmiDateFormat(locale: const Locale('en'), use24HourFormat: false, l10n: en);

  ServerConversation conversation(
    String id, {
    required DateTime startedAt,
    bool discarded = false,
    ConversationStatus status = ConversationStatus.completed,
    String title = '',
    String overview = '',
  }) =>
      ServerConversation(
        id: id,
        createdAt: startedAt,
        startedAt: startedAt,
        structured: Structured(title, overview),
        discarded: discarded,
        status: status,
      );

  group('Today', () {
    test('counts kept conversations of the local day and keeps both count lines', () {
      final now = DateTime(2026, 11, 25, 18);
      final doc = AndroidWidgetDocuments.today([
        conversation('a', startedAt: DateTime(2026, 11, 25, 9)),
        conversation('b', startedAt: DateTime(2026, 11, 25, 12)),
        conversation('gone', startedAt: DateTime(2026, 11, 25, 13), discarded: true),
        conversation('yesterday', startedAt: DateTime(2026, 11, 24, 23)),
      ], en, now: now);
      expect(doc['day'], '2026-11-25');
      expect(doc['conversations'], 2);
      expect(doc['count_label'], '2 conversations today');
      expect(doc['zero_label'], 'No conversations today');
      expect(doc['title'], 'Today');
    });
  });

  group('Summary', () {
    test('the newest finished conversation with an overview', () {
      final doc = AndroidWidgetDocuments.summary([
        conversation('live', startedAt: DateTime(2026), status: ConversationStatus.in_progress, overview: 'x'),
        conversation('empty', startedAt: DateTime(2026), title: 'No notes'),
        conversation('kept', startedAt: DateTime(2026), title: 'Standup', overview: ' Ship on Friday. '),
      ], en)!;
      expect(doc, {'title': 'Summary', 'id': 'kept', 'heading': 'Standup', 'text': 'Ship on Friday.'});
    });

    test('nothing to show without an overview', () {
      expect(AndroidWidgetDocuments.summary([conversation('a', startedAt: DateTime(2026))], en), isNull);
    });
  });

  group('Mute', () {
    test('listening offers Mute, muted offers Unmute, not capturing offers nothing', () {
      final live = AndroidWidgetDocuments.listening(RecordingState.deviceRecord, en);
      expect(live, containsPair('muted', false));
      expect(live, containsPair('available', true));
      expect(live['state'], 'Listening');
      expect(live['action'], 'Mute');
      final muted = AndroidWidgetDocuments.listening(RecordingState.pause, en);
      expect(muted['muted'], true);
      expect(muted['state'], 'Muted');
      expect(muted['action'], 'Unmute');
      expect(AndroidWidgetDocuments.listening(RecordingState.stop, en)['available'], false);
    });
  });

  group('Live translation', () {
    test('on with its target language, off without one', () {
      final on = AndroidWidgetDocuments.translation(on: true, language: 'English', l10n: en);
      expect(on['state'], 'On');
      expect(on['target'], 'Into English');
      expect(AndroidWidgetDocuments.translation(on: false, language: '', l10n: en)['target'], '');
    });
  });

  group('Tasks', () {
    test('adds the labels and a due line to the shared Up next document', () {
      final due = DateTime(2026, 11, 26, 9);
      final doc = AndroidWidgetDocuments.taskLabels({
        'tasks': [
          {'id': 't1', 'title': 'Call Chitapa', 'due': due.millisecondsSinceEpoch / 1000},
          {'id': 't2', 'title': 'Send the draft', 'due': null},
        ],
        'open': 5,
      }, en, dates);
      expect(doc['title'], 'Tasks');
      expect(doc['count_label'], '5');
      expect(doc['empty'], 'All caught up');
      final tasks = (doc['tasks'] as List).cast<Map<String, Object?>>();
      expect(tasks.first['due_label'], dates.dateTime(due));
      expect(tasks.last['due_label'], isNull);
      expect(tasks.first['id'], 't1', reason: 'the iOS fields stay as they were');
    });
  });

  group('Approvals', () {
    test('counts everything waiting and lists tasks, then events, then drafts, at most three', () {
      final waiting = WaitingForApproval(
        tasks: [
          const ActionItemWithMetadata(id: 't1', description: ' Book the venue ', completed: false),
          const ActionItemWithMetadata(id: 't2', description: '', completed: false),
        ],
        eventCount: 1,
        events: [
          FamilyCalendarEvent(
            conversationId: 'c1',
            eventIdx: 0,
            conversationTitle: 'Planning',
            title: 'Dentist',
            description: '',
            start: DateTime(2026, 11, 27, 10),
            duration: 1800,
          ),
        ],
        draftDates: const ['2026-11-24', '2026-11-23'],
      );
      final doc = AndroidWidgetDocuments.approvals(waiting, en, dates);
      expect(doc['count'], 5);
      expect(doc['title'], 'Waiting for Your OK');
      final items = (doc['items'] as List).cast<Map<String, Object?>>();
      expect(items.map((i) => i['title']), ['Book the venue', 'Dentist', startsWith('Day summary · ')]);
      expect(items.first['detail'], isNull, reason: 'a task without a due date has no detail line');
    });

    test('nothing waiting says so', () {
      final doc = AndroidWidgetDocuments.approvals(const WaitingForApproval(), en, dates);
      expect(doc['count'], 0);
      expect(doc['items'], isEmpty);
      expect(doc['empty'], 'Nothing is waiting for your OK.');
    });
  });

  test('every document is plain JSON', () {
    final now = DateTime(2026, 11, 25);
    for (final doc in [
      AndroidWidgetDocuments.today(const [], en, now: now),
      AndroidWidgetDocuments.capture(en),
      AndroidWidgetDocuments.listening(RecordingState.pause, en),
      AndroidWidgetDocuments.translation(on: true, language: 'Deutsch', l10n: en),
      AndroidWidgetDocuments.approvals(const WaitingForApproval(), en, dates),
    ]) {
      expect(jsonDecode(jsonEncode(doc)), doc);
    }
  });
}
