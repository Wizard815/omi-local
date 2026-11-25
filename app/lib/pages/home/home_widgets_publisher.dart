import 'dart:async';
import 'dart:io';

import 'package:flutter/widgets.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/providers/action_items_provider.dart';
import 'package:omi/providers/capture_provider.dart';
import 'package:omi/providers/conversation_provider.dart';
import 'package:omi/providers/device_provider.dart';
import 'package:omi/providers/home_provider.dart';
import 'package:omi/providers/user_provider.dart';
import 'package:omi/services/android_home_widgets.dart';
import 'package:omi/services/home_widgets_service.dart';
import 'package:omi/ui/format/omi_date_format.dart';
import 'package:omi/utils/enums.dart';

/// Keeps the Home Screen widgets in step with the app. iOS: the wearables (Devices), the next open
/// tasks (Up next) and the latest conversation (Latest). Android adds Today, Summary, Quick capture,
/// Mute, Live translation and Waiting for your OK. Changes are gathered for a moment and written
/// only when a document changed; leaving the app writes at once.
class HomeWidgetsPublisher with WidgetsBindingObserver {
  HomeWidgetsPublisher({
    required this.devices,
    required this.tasks,
    required this.conversations,
    required this.l10n,
    required this.dates,
    this.capture,
    this.user,
    this.home,
    HomeWidgetsService? service,
    this.delay = const Duration(milliseconds: 800),
    bool? android,
    DateTime Function()? now,
  })  : service = service ?? HomeWidgetsService.instance,
        android = android ?? Platform.isAndroid,
        _now = now ?? DateTime.now;

  final DeviceProvider devices;
  final ActionItemsProvider tasks;
  final ConversationProvider conversations;
  final AppLocalizations Function() l10n;
  final OmiDateFormat Function() dates;

  /// Android only: the listening state (Mute), the translation setting and its language.
  final CaptureProvider? capture;
  final UserProvider? user;
  final HomeProvider? home;
  final HomeWidgetsService service;
  final Duration delay;
  final bool android;
  final DateTime Function() _now;
  Timer? _timer;
  RecordingState? _lastRecordingState;

  List<Listenable> get _sources => [
        devices,
        tasks,
        conversations,
        if (android) ...[
          if (tasks.waiting case final waiting?) waiting,
          if (user case final user?) user,
          if (home case final home?) home
        ],
      ];

  void start() {
    for (final source in _sources) {
      source.addListener(_schedule);
    }
    // Capture notifies many times a second while recording; only a state change matters here.
    if (android) capture?.addListener(_onCapture);
    WidgetsBinding.instance.addObserver(this);
    _schedule();
  }

  void dispose() {
    _timer?.cancel();
    for (final source in _sources) {
      source.removeListener(_schedule);
    }
    if (android) capture?.removeListener(_onCapture);
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive || state == AppLifecycleState.paused) publishNow();
    // Coming back may be a new day for Today, and approvals may have changed on the server.
    if (state == AppLifecycleState.resumed && android) {
      unawaited(tasks.waiting?.load());
      _schedule();
    }
  }

  void _onCapture() {
    final state = capture?.recordingState;
    if (state == _lastRecordingState) return;
    _lastRecordingState = state;
    _schedule();
  }

  void _schedule() {
    _timer?.cancel();
    _timer = Timer(delay, publishNow);
  }

  /// Writes what the widgets show now. Up next waits for the task list to load (an empty list
  /// before then is not "All caught up"); Latest waits for a conversation to show.
  @visibleForTesting
  void publishNow() {
    _timer?.cancel();
    unawaited(
      service.publish(
        HomeWidgetsService.devicesKey,
        HomeWidgetsPayload.devices(
          saved: SharedPreferencesUtil().btDevices,
          connected: devices.connectedDevice,
          isConnected: devices.isConnected,
          battery: devices.batteryLevel,
          charging: devices.isCharging,
        ),
      ),
    );
    if (tasks.hasLoaded) {
      final open = tasks.incompleteItems;
      final upNext =
          HomeWidgetsPayload.upNext(HomeWidgetsPayload.pickUpNext(tasks.todayPreviewTasks(), open), open: open.length);
      unawaited(service.publish(
        HomeWidgetsService.upNextKey,
        android ? AndroidWidgetDocuments.taskLabels(upNext, l10n(), dates()) : upNext,
      ));
    }
    final latest = HomeWidgetsPayload.latestOf(conversations.conversations);
    if (latest != null) {
      unawaited(
        service.publish(HomeWidgetsService.latestKey, HomeWidgetsPayload.latest(latest, l10n(), dates: dates())),
      );
    }
    if (android) _publishAndroid();
  }

  void _publishAndroid() {
    final words = l10n();
    final list = conversations.conversations;
    _put(AndroidWidgetDocuments.todayKey, AndroidWidgetDocuments.today(list, words, now: _now()));
    final summary = AndroidWidgetDocuments.summary(list, words);
    if (summary != null) _put(AndroidWidgetDocuments.summaryKey, summary);
    _put(AndroidWidgetDocuments.captureKey, AndroidWidgetDocuments.capture(words));
    if (capture case final capture?) {
      _put(AndroidWidgetDocuments.listeningKey, AndroidWidgetDocuments.listening(capture.recordingState, words));
    }
    if (user case final user?) {
      final code = home?.userPrimaryLanguage ?? '';
      final language = code.isEmpty
          ? ''
          : home!.availableLanguages.entries.firstWhere((e) => e.value == code, orElse: () => MapEntry(code, code)).key;
      _put(
        AndroidWidgetDocuments.translationKey,
        AndroidWidgetDocuments.translation(on: !user.singleLanguageMode, language: language, l10n: words),
      );
    }
    final waiting = tasks.waiting;
    if (waiting != null && waiting.loaded) {
      _put(AndroidWidgetDocuments.approvalsKey, AndroidWidgetDocuments.approvals(waiting.state, words, dates()));
    }
  }

  void _put(String key, Map<String, Object?> document) => unawaited(service.publish(key, document));
}
