import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/backend/schema/schema.dart';
import 'package:omi/gen/assets.gen.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/ui/format/omi_date_format.dart';
import 'package:omi/ui/format/omi_duration.dart';
import 'package:omi/ui/format/speaker_names.dart';
import 'package:omi/utils/analytics/registry/events.g.dart' show ConversationUntitledRenderedSurface;
import 'package:omi/utils/conversations/conversation_title.dart';
import 'package:omi/utils/device.dart';
import 'package:omi/utils/logger.dart';
import 'package:omi/widgets/extensions/string.dart';

/// What the Home Screen widgets show, as the JSON documents the widget code reads
/// (iOS: ios/BatteryWidget/SharedDefaults.swift; Android: android/.../widgets/HomeWidgetStore.kt):
/// the reader's wearables, the next open tasks, the latest conversation, and on Android also the
/// live transcript, the latest day summary, the recent list and the mute state. Pure, so each
/// document can be checked without a phone.
abstract final class HomeWidgetsPayload {
  /// The picture the widget shows for [device]: the product photo the app shows, from the widget's
  /// assets. An Omi pendant is "device-omi", which the widget swaps for its lights-off photo while
  /// disconnected, as the device page does.
  static String image(BtDevice device) {
    final path = DeviceUtils.getDeviceImageFromBtDevice(device);
    final pictures = {
      Assets.images.omiWithoutRope.path: 'device-omi',
      Assets.images.appleWatch.path: 'device-apple-watch',
      Assets.images.beeDevice.path: 'device-bee',
      Assets.images.friendPendant.path: 'device-friend',
      Assets.images.omiDevkitWithoutRope.path: 'device-devkit',
      Assets.images.omiGlass.path: 'device-glass',
      Assets.images.plaudNotePin.path: 'device-plaud',
      Assets.images.raybanMeta.path: 'device-rayban',
      Assets.images.limitless.path: 'device-limitless',
      Assets.images.fieldy.path: 'device-fieldy',
      Assets.images.neoOne.path: 'device-neo',
    };
    return pictures[path] ?? 'device-omi';
  }

  /// The wearables the Devices widget pages through: the connected one first, then the most
  /// recently paired. Only the connected one has a live battery. The phone is never a device.
  static Map<String, Object?> devices({
    required List<BtDevice> saved,
    required BtDevice? connected,
    required bool isConnected,
    required int battery,
    required bool charging,
  }) {
    final ordered = <String, BtDevice>{};
    final live = isConnected && connected != null && connected.id.isNotEmpty ? connected : null;
    if (live != null) ordered[live.id] = live;
    for (final device in saved.reversed) {
      if (device.id.isNotEmpty) ordered.putIfAbsent(device.id, () => device);
    }
    return {
      'devices': [
        for (final device in ordered.values)
          {
            'id': device.id,
            'name': device.name.trim().isEmpty ? 'Omi' : device.name.trim(),
            'image': image(device),
            'connected': device.id == live?.id,
            'battery': device.id == live?.id && battery >= 0 ? battery.clamp(0, 100) : -1,
            'charging': device.id == live?.id && charging,
          },
      ],
    };
  }

  /// The tasks Up next shows: today's first, then the other open ones soonest due first (undated
  /// last), so what is coming shows ahead of its day (#5080).
  static List<ActionItemWithMetadata> pickUpNext(
    List<ActionItemWithMetadata> today,
    List<ActionItemWithMetadata> open, {
    int limit = 3,
  }) {
    final rest = open.where((task) => today.every((d) => d.id != task.id)).toList();
    // Stable: tasks without a due date keep the order the list gave them.
    final dated = rest.where((task) => task.dueAt != null).toList()..sort((a, b) => a.dueAt!.compareTo(b.dueAt!));
    return [...today, ...dated, ...rest.where((task) => task.dueAt == null)].take(limit).toList();
  }

  /// Up next: the tasks [pickUpNext] chose (at most [limit]) with when each is due, and how many
  /// are open in all.
  static Map<String, Object?> upNext(List<ActionItemWithMetadata> tasks, {required int open, int limit = 3}) {
    return {
      'tasks': [
        for (final task in tasks.take(limit))
          {
            'id': task.id,
            'title': task.description.trim(),
            'due': task.dueAt == null ? null : task.dueAt!.millisecondsSinceEpoch / 1000,
          },
      ],
      'open': open,
    };
  }

  /// The newest finished conversation that was kept (not discarded), or null when there is none.
  static ServerConversation? latestOf(List<ServerConversation> conversations) {
    for (final conversation in conversations) {
      if (conversation.discarded || conversation.deleted) continue;
      if (conversation.status != ConversationStatus.completed) continue;
      return conversation;
    }
    return null;
  }

  /// Latest: its title, when it started, and "1 task · 14 s" in the app's words.
  static Map<String, Object?>? latest(ServerConversation? conversation, AppLocalizations l10n, {OmiDateFormat? dates}) {
    if (conversation == null) return null;
    final title = conversationDisplayTitle(
      conversation,
      l10n,
      surface: ConversationUntitledRenderedSurface.homeWidget,
      title: conversation.structured.title.decodeString,
      dates: dates,
    );
    final tasks = conversation.structured.actionItems.where((item) => !item.deleted).length;
    final seconds = conversation.getDurationInSeconds();
    return {
      'id': conversation.id,
      'title': title,
      'at': (conversation.startedAt ?? conversation.createdAt).millisecondsSinceEpoch / 1000,
      'detail': [
        if (tasks > 0) l10n.tasksCountLabel(tasks),
        if (seconds > 0) OmiDuration.compact(seconds, l10n),
      ].join(' · '),
    };
  }

  /// Recent (Android): the newest kept, finished conversations, each as [latest] describes it.
  static Map<String, Object?> recent(
    List<ServerConversation> conversations,
    AppLocalizations l10n, {
    OmiDateFormat? dates,
    int limit = 5,
  }) {
    final kept = conversations.where(
      (c) => !c.discarded && !c.deleted && c.status == ConversationStatus.completed,
    );
    return {
      'conversations': [for (final c in kept.take(limit)) latest(c, l10n, dates: dates)],
    };
  }

  /// Live transcript (Android): the last [limit] segments of the capture in progress, each with
  /// the speaker label the app shows. [recording] false with no lines reads "nothing captured".
  static Map<String, Object?> transcript(
    List<TranscriptSegment> segments,
    AppLocalizations l10n, {
    required bool recording,
    String? recordingId,
    int limit = 6,
  }) {
    final tail = segments.length > limit ? segments.sublist(segments.length - limit) : segments;
    return {
      'recording': recording,
      'recording_id': recordingId,
      'lines': [
        for (final segment in tail)
          if (segment.text.trim().isNotEmpty)
            {
              'speaker': SpeakerNames.excerptLabel(isUser: segment.isUser, speakerLabel: segment.speaker, l10n: l10n),
              'text': segment.text.trim(),
            },
      ],
    };
  }

  /// Day summary (Android): the latest day review's title, the start of its text and its tags;
  /// null clears the widget to "no day summary yet".
  static Map<String, Object?>? daySummary(DayReview? review, {int textLimit = 600}) {
    if (review == null) return null;
    final text = review.finalText.trim();
    return {
      'date': review.date,
      'title': review.title.trim(),
      'text': text.length > textLimit ? '${text.substring(0, textLimit).trimRight()}…' : text,
      'tags': review.tags.take(6).toList(),
    };
  }

  /// Mute (Android): whether capture is muted and whether there is a capture to mute at all.
  static Map<String, Object?> mute({required bool muted, required String? source}) {
    return {'muted': muted, 'available': source != null, 'source': source};
  }
}

/// Writes the widget documents through the platform channel, only when one changed, and asks
/// the OS to redraw that widget: iOS through the battery widget's channel into the App Group,
/// Android through `com.omi/home_widgets` into the widgets' preferences file. Nothing happens on
/// other platforms. On Android the widgets also call back ([onAction]) and leave an action behind
/// when the app was closed ([consumePendingAction]).
class HomeWidgetsService {
  HomeWidgetsService._() : _bridge = _platformBridge;

  @visibleForTesting
  HomeWidgetsService.testing(this._bridge);

  static final HomeWidgetsService instance = HomeWidgetsService._();

  static const MethodChannel _iosChannel = MethodChannel('com.omi.battery_widget');
  static const MethodChannel _androidChannel = MethodChannel('com.omi/home_widgets');

  static const String devicesKey = 'widget_devices';
  static const String upNextKey = 'widget_up_next';
  static const String latestKey = 'widget_latest';
  // Android only.
  static const String recentKey = 'widget_recent';
  static const String transcriptKey = 'widget_transcript';
  static const String daySummaryKey = 'widget_day_summary';
  static const String muteKey = 'widget_mute';

  /// The one action an Android widget can ask of the app.
  static const String toggleMuteAction = 'toggle_mute';

  final Future<Object?> Function(String method, Map<String, Object?> arguments) _bridge;
  final Map<String, String?> _published = {};
  bool _listening = false;

  /// Called with a widget's action (`toggle_mute`) while the app runs.
  void Function(String action)? onAction;

  static Future<Object?> _platformBridge(String method, Map<String, Object?> arguments) async {
    if (Platform.isIOS) return _iosChannel.invokeMethod<Object?>(method, arguments);
    if (Platform.isAndroid) return _androidChannel.invokeMethod<Object?>(method, arguments);
    return null;
  }

  static bool get _supported => Platform.isIOS || Platform.isAndroid;

  /// Publishes [document] under [key]; null clears it (the widget then asks to open Omi).
  Future<void> publish(String key, Map<String, Object?>? document) async {
    if (!_supported) return;
    final json = document == null ? null : jsonEncode(document);
    if (_published.containsKey(key) && _published[key] == json) return;
    _published[key] = json;
    try {
      await _bridge('updateWidgetData', {'key': key, 'json': json});
    } catch (e) {
      _published.remove(key);
      Logger.debug('HomeWidgetsService.publish($key) failed: $e');
    }
  }

  /// Starts taking the Android widgets' actions, and runs the one left while the app was closed.
  Future<void> listen() async {
    if (!Platform.isAndroid) return;
    if (!_listening) {
      _listening = true;
      _androidChannel.setMethodCallHandler((call) async {
        if (call.method != 'widgetAction') return null;
        final action = (call.arguments as Map?)?['action'];
        if (action is String) onAction?.call(action);
        return null;
      });
    }
    final pending = await consumePendingAction();
    if (pending != null) onAction?.call(pending);
  }

  /// The action a widget asked for while no engine was alive, once; null when there is none.
  Future<String?> consumePendingAction() async {
    if (!Platform.isAndroid) return null;
    try {
      return await _bridge('consumePendingAction', const {}) as String?;
    } catch (e) {
      Logger.debug('HomeWidgetsService.consumePendingAction failed: $e');
      return null;
    }
  }

  /// Signing out: no tasks, transcript or conversation titles stay on the Home Screen.
  Future<void> clear() async {
    await Future.wait([
      for (final key in [upNextKey, latestKey, devicesKey, recentKey, transcriptKey, daySummaryKey, muteKey])
        publish(key, null),
    ]);
  }
}
