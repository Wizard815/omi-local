import 'dart:io';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';

import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/backend/schema/schema.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/services/home_widgets_service.dart';
import 'package:omi/ui/format/omi_date_format.dart';

/// What the iOS Home Screen widgets are given (ios/BatteryWidget/SharedDefaults.swift reads it).
void main() {
  setUpAll(() => initializeDateFormatting());
  final en = lookupAppLocalizations(const Locale('en'));

  BtDevice device(String id, String name, DeviceType type) => BtDevice(id: id, name: name, type: type, rssi: -50);

  group('Devices', () {
    test('the connected wearable first, then the most recently paired; battery only where live', () {
      final omi = device('omi-1', 'Omi', DeviceType.omi);
      final plaud = device('plaud-1', 'PLAUD NotePin', DeviceType.plaud);
      final bee = device('bee-1', 'Bee', DeviceType.bee);
      final doc = HomeWidgetsPayload.devices(
        saved: [omi, plaud, bee], // paired in this order
        connected: plaud,
        isConnected: true,
        battery: 42,
        charging: true,
      );
      final devices = (doc['devices'] as List).cast<Map<String, Object?>>();
      expect(devices.map((d) => d['id']), ['plaud-1', 'bee-1', 'omi-1']);
      expect(devices.first, {
        'id': 'plaud-1',
        'name': 'PLAUD NotePin',
        'image': 'device-plaud',
        'connected': true,
        'battery': 42,
        'charging': true,
      });
      expect(devices[1]['connected'], false);
      expect(devices[1]['battery'], -1, reason: 'a device that is not connected has no live battery');
      expect(devices[2]['image'], 'device-omi', reason: 'an Omi pendant shows the photo the app shows');
    });

    test('nothing connected: every paired device, none live; the phone is never listed', () {
      final omi = device('omi-1', '', DeviceType.omi);
      final doc = HomeWidgetsPayload.devices(
        saved: [omi],
        connected: null,
        isConnected: false,
        battery: 80,
        charging: false,
      );
      final devices = (doc['devices'] as List).cast<Map<String, Object?>>();
      expect(devices, hasLength(1));
      expect(devices.single['name'], 'Omi', reason: 'an unnamed pendant still has a name');
      expect(devices.single['connected'], false);
      expect(devices.single['battery'], -1);
    });

    test('each product has its own picture', () {
      expect(HomeWidgetsPayload.image(device('o', 'Omi', DeviceType.omi)), 'device-omi');
      expect(HomeWidgetsPayload.image(device('k', 'Omi DevKit 2', DeviceType.omi)), 'device-devkit');
      expect(HomeWidgetsPayload.image(device('a', 'Watch', DeviceType.appleWatch)), 'device-apple-watch');
      expect(HomeWidgetsPayload.image(device('b', 'Limitless', DeviceType.limitless)), 'device-limitless');
      expect(HomeWidgetsPayload.image(device('c', 'Fieldy', DeviceType.fieldy)), 'device-fieldy');
      expect(HomeWidgetsPayload.image(device('d', 'Friend', DeviceType.friendPendant)), 'device-friend');
      expect(HomeWidgetsPayload.image(device('e', 'Ray-Ban', DeviceType.raybanMeta)), 'device-rayban');
      expect(HomeWidgetsPayload.image(device('f', 'Omi Glass', DeviceType.openglass)), 'device-glass');
    });
  });

  group('Up next picks', () {
    ActionItemWithMetadata task(String id, {DateTime? due}) =>
        ActionItemWithMetadata(id: id, description: id, completed: false, dueAt: due);

    test("today's tasks first, then the rest soonest due first, undated last (#5080)", () {
      final today = [task('today')];
      final open = [
        task('undated'),
        task('next-week', due: DateTime.utc(2026, 10, 3)),
        task('today'),
        task('tomorrow', due: DateTime.utc(2026, 9, 27)),
      ];
      expect(HomeWidgetsPayload.pickUpNext(today, open).map((t) => t.id), ['today', 'tomorrow', 'next-week']);
      expect(HomeWidgetsPayload.pickUpNext(today, open, limit: 5).map((t) => t.id), [
        'today',
        'tomorrow',
        'next-week',
        'undated',
      ]);
    });

    test('nothing due today: upcoming tasks still show ahead of their day', () {
      final open = [task('undated'), task('friday', due: DateTime.utc(2026, 10, 2))];
      expect(HomeWidgetsPayload.pickUpNext(const [], open).map((t) => t.id), ['friday', 'undated']);
    });
  });

  test('Up next: the first tasks with their due time, and how many are open', () {
    final due = DateTime.utc(2026, 9, 26, 21);
    final doc = HomeWidgetsPayload.upNext([
      ActionItemWithMetadata(id: 't1', description: ' Call Chitapa ', completed: false, dueAt: due),
      const ActionItemWithMetadata(id: 't2', description: 'Send the pricing draft', completed: false),
      const ActionItemWithMetadata(id: 't3', description: 'Third', completed: false),
      const ActionItemWithMetadata(id: 't4', description: 'Fourth', completed: false),
    ], open: 7);
    expect(doc['open'], 7);
    final tasks = (doc['tasks'] as List).cast<Map<String, Object?>>();
    expect(tasks.map((t) => t['id']), ['t1', 't2', 't3']);
    expect(tasks.first, {'id': 't1', 'title': 'Call Chitapa', 'due': due.millisecondsSinceEpoch / 1000});
    expect(tasks[1]['due'], isNull);
  });

  group('Latest', () {
    ServerConversation conversation(
      String id, {
      bool discarded = false,
      ConversationStatus? status,
      String title = '',
    }) {
      final structured = Structured(title, '')..actionItems = [ActionItem('Call back')];
      return ServerConversation(
        id: id,
        createdAt: DateTime.utc(2026, 9, 26, 19, 14),
        structured: structured,
        discarded: discarded,
        status: status ?? ConversationStatus.completed,
        startedAt: DateTime.utc(2026, 9, 26, 19, 14),
        finishedAt: DateTime.utc(2026, 9, 26, 19, 14, 14),
      );
    }

    test('the newest kept, finished conversation', () {
      final list = [
        conversation('live', status: ConversationStatus.in_progress),
        conversation('gone', discarded: true),
        conversation('kept', title: 'Call Chitapa reminder'),
      ];
      expect(HomeWidgetsPayload.latestOf(list)?.id, 'kept');
      expect(HomeWidgetsPayload.latestOf([conversation('gone', discarded: true)]), isNull);
    });

    test('its title, when it started, and "1 task · 14s" in the app\'s words', () {
      final doc = HomeWidgetsPayload.latest(conversation('kept', title: 'Call Chitapa reminder'), en)!;
      expect(doc['title'], 'Call Chitapa reminder');
      expect(doc['at'], DateTime.utc(2026, 9, 26, 19, 14).millisecondsSinceEpoch / 1000);
      expect(doc['detail'], startsWith('1 task · '));
      expect(HomeWidgetsPayload.latest(conversation('untitled'), en)!['title'], isNot(contains('·')));
      expect(HomeWidgetsPayload.latest(null, en), isNull);
    });

    test('an untitled conversation honors the device 24-hour clock setting', () {
      final date = OmiDateFormat(locale: const Locale('en'), use24HourFormat: true, l10n: en);
      final doc = HomeWidgetsPayload.latest(conversation('untitled'), en, dates: date)!;
      final startedAt = DateTime.utc(2026, 9, 26, 19, 14).toLocal();
      expect(doc['title'], date.dateTime(startedAt));
      expect(doc['title'], isNot(contains('AM')));
      expect(doc['title'], isNot(contains('PM')));
    });

    test('Recent (Android): the newest kept, finished ones, at most five, as Latest describes them', () {
      final list = [
        conversation('live', status: ConversationStatus.in_progress),
        conversation('gone', discarded: true),
        for (var i = 0; i < 7; i++) conversation('kept-$i', title: 'Talk $i'),
      ];
      final doc = HomeWidgetsPayload.recent(list, en);
      final rows = (doc['conversations'] as List).cast<Map<String, Object?>>();
      expect(rows.map((r) => r['id']), ['kept-0', 'kept-1', 'kept-2', 'kept-3', 'kept-4']);
      expect(rows.first['title'], 'Talk 0');
      expect(rows.first['detail'], startsWith('1 task · '));
    });
  });

  group('Android documents', () {
    TranscriptSegment segment(String text, {String? speaker, bool isUser = false}) => TranscriptSegment(
          id: text,
          text: text,
          speaker: speaker,
          isUser: isUser,
          personId: null,
          start: 0,
          end: 1,
          translations: const [],
        );

    test('Live transcript: the last lines with the speaker labels the app shows; blanks dropped', () {
      final doc = HomeWidgetsPayload.transcript(
        [
          segment('old'),
          segment('  '),
          segment('one', speaker: 'SPEAKER_01'),
          segment('two', isUser: true),
          segment('three', speaker: 'Chitapa'),
        ],
        en,
        recording: true,
        recordingId: 'rec-1',
        limit: 4,
      );
      expect(doc['recording'], true);
      expect(doc['recording_id'], 'rec-1');
      final lines = (doc['lines'] as List).cast<Map<String, Object?>>();
      expect(lines.map((l) => l['text']), ['one', 'two', 'three'],
          reason: 'the oldest line fell off, the blank one too');
      expect(lines[1]['speaker'], en.you);
      expect(lines[2]['speaker'], 'Chitapa');
    });

    test('Day summary: title, the start of the text, up to six tags; none clears the widget', () {
      final review = DayReview(
        date: '2026-10-27',
        title: ' A quiet Tuesday ',
        summaryText: 'x' * 700,
        finalText: 'y' * 700,
        corrections: const [],
        tags: const ['work', 'home', 'a', 'b', 'c', 'd', 'e'],
        keptConversationIds: const [],
        rolledUpConversationIds: const [],
      );
      final doc = HomeWidgetsPayload.daySummary(review)!;
      expect(doc['date'], '2026-10-27');
      expect(doc['title'], 'A quiet Tuesday');
      expect((doc['text'] as String).length, 601, reason: '600 characters of the corrected text, then an ellipsis');
      expect((doc['text'] as String), startsWith('yyy'));
      expect(doc['tags'], ['work', 'home', 'a', 'b', 'c', 'd']);
      expect(HomeWidgetsPayload.daySummary(null), isNull);
    });

    test('Mute: the state, and whether there is a capture to mute at all', () {
      expect(
          HomeWidgetsPayload.mute(muted: true, source: 'phone'), {'muted': true, 'available': true, 'source': 'phone'});
      expect(HomeWidgetsPayload.mute(muted: false, source: null)['available'], false);
    });
  });

  group('HomeWidgetsService', () {
    test('publishes a document once until it changes, and clears every key on sign-out', () async {
      final calls = <(String, Map<String, Object?>)>[];
      final service = HomeWidgetsService.testing((method, args) async {
        calls.add((method, args));
        return null;
      });
      // Off iOS and Android nothing is published; the dedupe and the key set are what is checked here.
      await service.publish(HomeWidgetsService.muteKey, {'muted': true});
      await service.publish(HomeWidgetsService.muteKey, {'muted': true});
      await service.clear();
      if (!Platform.isIOS && !Platform.isAndroid) {
        expect(calls, isEmpty);
        return;
      }
      expect(calls.where((c) => c.$1 == 'updateWidgetData' && c.$2['json'] != null), hasLength(1));
      expect(
          calls.where((c) => c.$2['json'] == null).map((c) => c.$2['key']),
          containsAll([
            HomeWidgetsService.transcriptKey,
            HomeWidgetsService.daySummaryKey,
            HomeWidgetsService.recentKey,
            HomeWidgetsService.muteKey,
          ]));
    });
  });
}
