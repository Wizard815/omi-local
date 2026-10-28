import 'dart:async';

import 'package:flutter/widgets.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/providers/action_items_provider.dart';
import 'package:omi/providers/capture_provider.dart';
import 'package:omi/providers/conversation_provider.dart';
import 'package:omi/providers/device_provider.dart';
import 'package:omi/services/home_widgets_service.dart';
import 'package:omi/ui/format/omi_date_format.dart';

/// Keeps the Home Screen widgets in step with the app: the wearables (Devices), the next open
/// tasks (Up next / To do) and the latest conversation (Latest); on Android also the live
/// transcript, the mute state, the recent list and the latest day summary. Changes are gathered
/// for a moment and written only when a document changed; leaving the app writes at once.
class HomeWidgetsPublisher with WidgetsBindingObserver {
  HomeWidgetsPublisher({
    required this.devices,
    required this.tasks,
    required this.conversations,
    required this.l10n,
    required this.dates,
    this.capture,
    this.latestDayReview,
    HomeWidgetsService? service,
    this.delay = const Duration(milliseconds: 800),
    this.android = false,
    this.dayReviewRefresh = const Duration(minutes: 30),
  }) : service = service ?? HomeWidgetsService.instance;

  final DeviceProvider devices;
  final ActionItemsProvider tasks;
  final ConversationProvider conversations;

  /// The live capture (Android: Live transcript and Mute widgets).
  final CaptureProvider? capture;

  /// Fetches the newest day review (Android: Day summary widget); null when the server has none.
  final Future<DayReview?> Function()? latestDayReview;
  final AppLocalizations Function() l10n;
  final OmiDateFormat Function() dates;
  final HomeWidgetsService service;
  final Duration delay;

  /// Publish the Android-only documents too (five tasks instead of three in Up next).
  final bool android;

  /// How long a fetched day summary is kept before the next publish asks the server again.
  final Duration dayReviewRefresh;
  Timer? _timer;
  DateTime? _dayReviewFetchedAt;
  bool _fetchingDayReview = false;

  void start() {
    devices.addListener(_schedule);
    tasks.addListener(_schedule);
    conversations.addListener(_schedule);
    // omi-perf-allow: unselected-provider-listener -- gathers changes for 0.8 s and publishes only documents that changed; nothing rebuilds
    capture?.addListener(_schedule);
    WidgetsBinding.instance.addObserver(this);
    _schedule();
  }

  void dispose() {
    _timer?.cancel();
    devices.removeListener(_schedule);
    tasks.removeListener(_schedule);
    conversations.removeListener(_schedule);
    capture?.removeListener(_schedule);
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive || state == AppLifecycleState.paused) publishNow();
    // Coming back may follow a bundle run on the server: ask again for the day summary.
    if (state == AppLifecycleState.resumed) {
      _dayReviewFetchedAt = null;
      _schedule();
    }
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
      final limit = android ? 5 : 3;
      unawaited(
        service.publish(
          HomeWidgetsService.upNextKey,
          HomeWidgetsPayload.upNext(
            HomeWidgetsPayload.pickUpNext(tasks.todayPreviewTasks(), open, limit: limit),
            open: open.length,
            limit: limit,
          ),
        ),
      );
    }
    final latest = HomeWidgetsPayload.latestOf(conversations.conversations);
    if (latest != null) {
      unawaited(
        service.publish(HomeWidgetsService.latestKey, HomeWidgetsPayload.latest(latest, l10n(), dates: dates())),
      );
    }
    if (!android) return;
    if (latest != null) {
      unawaited(
        service.publish(
          HomeWidgetsService.recentKey,
          HomeWidgetsPayload.recent(conversations.conversations, l10n(), dates: dates()),
        ),
      );
    }
    final capture = this.capture;
    if (capture != null) {
      final source = capture.liveCaptureSource;
      unawaited(service.publish(
          HomeWidgetsService.muteKey, HomeWidgetsPayload.mute(muted: capture.isPaused, source: source)));
      unawaited(
        service.publish(
          HomeWidgetsService.transcriptKey,
          HomeWidgetsPayload.transcript(
            capture.segments,
            l10n(),
            recording: source != null && !capture.isPaused,
            recordingId: capture.activeRecordingId,
          ),
        ),
      );
    }
    _publishDaySummary();
  }

  /// The day summary comes from the server, so it is fetched at most once per [dayReviewRefresh]
  /// (and again on resume), never on every provider change.
  void _publishDaySummary() {
    final fetch = latestDayReview;
    if (fetch == null || _fetchingDayReview) return;
    final fetchedAt = _dayReviewFetchedAt;
    if (fetchedAt != null && DateTime.now().difference(fetchedAt) < dayReviewRefresh) return;
    _fetchingDayReview = true;
    unawaited(
      fetch().then((review) {
        _dayReviewFetchedAt = DateTime.now();
        return service.publish(HomeWidgetsService.daySummaryKey, HomeWidgetsPayload.daySummary(review));
      }).catchError((Object e) {
        // Left for the next publish; the widget keeps the last summary it had.
      }).whenComplete(() => _fetchingDayReview = false),
    );
  }
}
