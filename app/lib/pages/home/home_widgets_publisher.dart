import 'dart:async';
import 'dart:io';

import 'package:flutter/widgets.dart';

import 'package:omi/backend/http/api/family_calendar.dart' as family_calendar_api;
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/preferences.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/providers/action_items_provider.dart';
import 'package:omi/providers/capture_provider.dart';
import 'package:omi/providers/conversation_provider.dart';
import 'package:omi/providers/device_provider.dart';
import 'package:omi/providers/home_provider.dart';
import 'package:omi/providers/memories_provider.dart';
import 'package:omi/providers/user_provider.dart';
import 'package:omi/services/android_home_widgets.dart';
import 'package:omi/services/home_widgets_service.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';
import 'package:omi/services/phone_llm/phone_llm_status.dart';
import 'package:omi/services/today/feed_parser.dart';
import 'package:omi/services/today/news_feed_service.dart';
import 'package:omi/services/today/today_models.dart';
import 'package:omi/ui/format/omi_date_format.dart';
import 'package:omi/utils/enums.dart';

/// Keeps the Home Screen widgets in step with the app. iOS: the wearables (Devices), the next open
/// tasks (Up next) and the latest conversation (Latest). Android adds Today, Summary, Quick capture,
/// Mute, Live translation, Waiting for your OK, Family day, Next event, Memory and AI source. Changes are
/// gathered for a moment and written
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
    this.memories,
    Future<FamilyDay?> Function()? loadFamilyDay,
    this.familyDayEvery = const Duration(minutes: 15),
    this.phoneLlm,
    this.phoneStatus,
    this.phoneCheckEvery = const Duration(minutes: 1),
    Future<List<FeedItem>> Function()? loadHeadlines,
    HomeWidgetsService? service,
    this.delay = const Duration(milliseconds: 800),
    bool? android,
    DateTime Function()? now,
  })  : service = service ?? HomeWidgetsService.instance,
        android = android ?? Platform.isAndroid,
        loadFamilyDay = loadFamilyDay ?? _fetchFamilyDay,
        loadHeadlines = loadHeadlines ?? _cachedHeadlines,
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

  /// Android only: the Memory widget's memories.
  final MemoriesProvider? memories;

  /// Android only: today on the family calendar (Family day, Next event). Null = no answer.
  final Future<FamilyDay?> Function() loadFamilyDay;

  /// Android only: the newest headlines Omi Today cached (never fetched here).
  final Future<List<FeedItem>> Function() loadHeadlines;
  List<FeedItem> _headlines = const [];
  FamilyDay? _familyDay;

  /// How often the family calendar is asked again while the app is open.
  final Duration familyDayEvery;

  /// Android only: the AI source and the phone model's state, checked every [phoneCheckEvery]
  /// while the app is in front (never in the background).
  final PhoneLlmSettings? phoneLlm;
  final PhoneLlmStatus? phoneStatus;
  final Duration phoneCheckEvery;
  Timer? _phoneTimer;
  final HomeWidgetsService service;
  final Duration delay;
  final bool android;
  final DateTime Function() _now;
  Timer? _timer;
  Timer? _familyTimer;
  RecordingState? _lastRecordingState;
  bool _familyPublished = false;
  bool _familyLoading = false;

  List<Listenable> get _sources => [
        devices,
        tasks,
        conversations,
        if (android) ...[
          if (tasks.waiting case final waiting?) waiting,
          if (user case final user?) user,
          if (home case final home?) home,
          if (memories case final memories?) memories,
          if (phoneLlm case final phoneLlm?) phoneLlm,
          if (phoneStatus case final phoneStatus?) phoneStatus,
        ],
      ];

  void start() {
    for (final source in _sources) {
      source.addListener(_schedule);
    }
    // Capture notifies many times a second while recording; only a state change matters here.
    if (android) {
      capture?.addListener(_onCapture);
      if (memories case final memories? when !memories.hasLoaded) unawaited(memories.loadMemories());
      unawaited(refreshFamilyDay());
      unawaited(refreshHeadlines());
      _familyTimer = Timer.periodic(familyDayEvery, (_) => unawaited(refreshFamilyDay()));
    }
    WidgetsBinding.instance.addObserver(this);
    if (android) {
      unawaited(phoneLlm?.ensureLoaded());
      _checkPhone();
    }
    _schedule();
  }

  void _checkPhone() {
    final status = phoneStatus;
    if (status == null) return;
    unawaited(status.check());
    _phoneTimer?.cancel();
    _phoneTimer = Timer.periodic(phoneCheckEvery, (_) => unawaited(status.check()));
  }

  void dispose() {
    _timer?.cancel();
    _familyTimer?.cancel();
    _phoneTimer?.cancel();
    for (final source in _sources) {
      source.removeListener(_schedule);
    }
    if (android) capture?.removeListener(_onCapture);
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive || state == AppLifecycleState.paused) publishNow();
    if (state == AppLifecycleState.paused) _phoneTimer?.cancel();
    // Coming back may be a new day for Today, and approvals may have changed on the server.
    if (state == AppLifecycleState.resumed && android) {
      unawaited(tasks.waiting?.load());
      unawaited(refreshFamilyDay());
      unawaited(refreshHeadlines());
      _checkPhone();
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
    final phone = phoneLlm;
    if (phone != null && phone.loaded) {
      _put(
        AndroidWidgetDocuments.aiSourceKey,
        AndroidWidgetDocuments.aiSource(
          mode: phone.mode,
          health: phoneStatus?.health,
          checkedAt: phoneStatus?.checkedAt,
          l10n: words,
          dates: dates(),
        ),
      );
    }
    final waiting = tasks.waiting;
    if (waiting != null && waiting.loaded) {
      _put(AndroidWidgetDocuments.approvalsKey, AndroidWidgetDocuments.approvals(waiting.state, words, dates()));
    }
    _put(AndroidWidgetDocuments.nextEventKey, AndroidWidgetDocuments.nextEvent(words));
    if (memories case final memories? when memories.hasLoaded) {
      _put(AndroidWidgetDocuments.memoryKey, AndroidWidgetDocuments.memory(memories.memories, words));
    }
    if (tasks.hasLoaded) {
      final now = _now();
      _put(
        AndroidWidgetDocuments.overviewKey,
        AndroidWidgetDocuments.overview(
          agenda: TodayAgenda.build(deviceEvents: const [], family: _familyDay, tasks: tasks.reminderTasks, now: now),
          summary: TodayDaySummary.from(list, now: now),
          headlines: _headlines,
          l10n: words,
          dates: dates(),
          now: now,
        ),
      );
    }
  }

  /// Reads the headlines Omi Today last fetched, for the Today overview widget.
  @visibleForTesting
  Future<void> refreshHeadlines() async {
    if (!android) return;
    try {
      _headlines = await loadHeadlines();
      _schedule();
    } catch (_) {}
  }

  static Future<List<FeedItem>> _cachedHeadlines() async =>
      (await NewsFeedService.instance.cached())?.items ?? const [];

  /// Asks the server for today on the family calendar and writes Family day. An answer without the
  /// gateway (down, refused) keeps what the widget already shows; the first one is still written so
  /// the widget can say why it is empty.
  @visibleForTesting
  Future<void> refreshFamilyDay() async {
    if (!android || _familyLoading) return;
    _familyLoading = true;
    try {
      final day = await loadFamilyDay();
      if (day == null) return;
      if (day.reachable || !day.configured) _familyDay = day;
      if (day.configured && !day.reachable && _familyPublished) return;
      _put(AndroidWidgetDocuments.familyDayKey, AndroidWidgetDocuments.familyDay(day, l10n(), now: _now()));
      _familyPublished = true;
    } catch (_) {
      // A widget keeps its last data; the next refresh tries again.
    } finally {
      _familyLoading = false;
    }
  }

  static Future<FamilyDay?> _fetchFamilyDay() async => switch (await family_calendar_api.getFamilyDay()) {
        ApiSuccess<FamilyDay>(:final data) => data,
        _ => null,
      };

  void _put(String key, Map<String, Object?> document) => unawaited(service.publish(key, document));
}
