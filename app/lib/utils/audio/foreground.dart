import 'dart:io';

import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:geolocator/geolocator.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/utils/logger.dart';
import 'package:omi/utils/notification_channel_strings.dart';

@pragma('vm:entry-point')
void _startForegroundCallback() {
  FlutterForegroundTask.setTaskHandler(_ForegroundFirstTaskHandler());
}

class _ForegroundFirstTaskHandler extends TaskHandler {
  DateTime? _locationUpdatedAt;

  static const Duration _lastKnownMaxAge = Duration(minutes: 5);

  bool _isLastKnownFresh(Position position) {
    final age = DateTime.now().toUtc().difference(position.timestamp.toUtc());
    return !age.isNegative && age <= _lastKnownMaxAge;
  }

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter taskStarter) async {
    Logger.debug("Starting foreground task");
    _locationInBackground();
  }

  Future _locationInBackground() async {
    // Periodic refresh from FOREGROUND_SERVICE_LOCATION. while-in-use is
    // enough; do not request ACCESS_BACKGROUND_LOCATION (Play Store
    // prominent-disclosure). This isolate has no Activity, so it never prompts.
    if (await Geolocator.isLocationServiceEnabled()) {
      final permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.always || permission == LocationPermission.whileInUse) {
        Position? lastKnown;
        try {
          lastKnown = await Geolocator.getLastKnownPosition() ??
              await Geolocator.getLastKnownPosition(forceAndroidLocationManager: true);
        } catch (_) {}
        late final Position locationData;
        if (lastKnown != null && _isLastKnownFresh(lastKnown)) {
          locationData = lastKnown;
        } else {
          try {
            locationData = await Geolocator.getCurrentPosition(
              locationSettings: const LocationSettings(accuracy: LocationAccuracy.medium),
            ).timeout(const Duration(seconds: 8));
          } catch (e) {
            if (lastKnown == null) {
              Object loc = {'error': 'Location fix failed: $e'};
              FlutterForegroundTask.sendDataToMain(loc);
              return;
            }
            locationData = lastKnown;
          }
        }
        if (_locationUpdatedAt == null ||
            _locationUpdatedAt!.isBefore(DateTime.now().subtract(const Duration(minutes: 5)))) {
          Object loc = {
            "latitude": locationData.latitude,
            "longitude": locationData.longitude,
            'altitude': locationData.altitude,
            'accuracy': locationData.accuracy,
            'time': locationData.timestamp.toUtc().toIso8601String(),
          };
          FlutterForegroundTask.sendDataToMain(loc);
          _locationUpdatedAt = DateTime.now();
        }
      } else {
        Object loc = {'error': 'Location permission is not granted'};
        FlutterForegroundTask.sendDataToMain(loc);
      }
    } else {
      Object loc = {'error': 'Location service is not enabled'};
      FlutterForegroundTask.sendDataToMain(loc);
    }
  }

  @override
  void onReceiveData(Object data) async {
    Logger.debug('onReceiveData: $data');
    await _locationInBackground();
  }

  @override
  void onRepeatEvent(DateTime timestamp) async {
    Logger.debug("Foreground repeat event triggered");
    await _locationInBackground();
  }

  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {
    Logger.debug("Destroying foreground task");
    FlutterForegroundTask.stopService();
  }
}

class ForegroundUtil {
  static bool _isInitialized = false;
  static bool _isStarting = false;

  static Future<void> requestPermissions() async {
    // Android 13+, you need to allow notification permission to display foreground service notification.
    //
    // iOS: If you need notification, ask for permission.
    final NotificationPermission notificationPermissionStatus =
        await FlutterForegroundTask.checkNotificationPermission();
    if (notificationPermissionStatus != NotificationPermission.granted) {
      await FlutterForegroundTask.requestNotificationPermission();
    }

    if (Platform.isAndroid) {
      // if (!await FlutterForegroundTask.canDrawOverlays) {
      //   await FlutterForegroundTask.openSystemAlertWindowSettings();
      // }
      if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
        await FlutterForegroundTask.requestIgnoreBatteryOptimization();
      }
    }
  }

  Future<bool> get isIgnoringBatteryOptimizations async => await FlutterForegroundTask.isIgnoringBatteryOptimizations;

  static Future<void> initializeForegroundService() async {
    if (_isInitialized) {
      Logger.debug('ForegroundService already initialized, skipping');
      return;
    }

    if (await FlutterForegroundTask.isRunningService) {
      _isInitialized = true;
      return;
    }

    Logger.debug('initializeForegroundService');

    try {
      await NotificationChannelStrings.loadAppLocale();
      FlutterForegroundTask.init(
        androidNotificationOptions: AndroidNotificationOptions(
          channelId: 'foreground_service',
          channelName: NotificationChannelStrings.foregroundServiceChannelName,
          channelDescription: NotificationChannelStrings.foregroundServiceChannelDescription,
          channelImportance: NotificationChannelImportance.LOW,
          priority: NotificationPriority.HIGH,
          // iconData: const NotificationIconData(
          //   resType: ResourceType.mipmap,
          //   resPrefix: ResourcePrefix.ic,
          //   name: 'launcher',
          // ),
        ),
        iosNotificationOptions: const IOSNotificationOptions(showNotification: false, playSound: false),
        foregroundTaskOptions: ForegroundTaskOptions(
          // Warn: 5m, for location tracking. If we want to support other services, we use the differenct interval,
          // such as 1m + self-validation in each service.
          eventAction: ForegroundTaskEventAction.repeat(60 * 1000 * 5),
          autoRunOnBoot: false,
          allowWakeLock: false,
          allowWifiLock: false,
        ),
      );
      _isInitialized = true;
      Logger.debug('ForegroundService initialized successfully');
    } catch (e) {
      Logger.debug('ForegroundService initialization failed: $e');
      _isInitialized = false;
    }
  }

  static Future<ServiceRequestResult> startForegroundTask() async {
    if (_isStarting) {
      Logger.debug('ForegroundTask already starting, skipping');
      return const ServiceRequestSuccess();
    }

    _isStarting = true;
    Logger.debug('startForegroundTask');

    try {
      ServiceRequestResult result;
      if (await FlutterForegroundTask.isRunningService) {
        result = await FlutterForegroundTask.restartService();
      } else {
        result = await FlutterForegroundTask.startService(
          notificationTitle: 'Your Omi Device is connected.',
          notificationText: 'Transcription service is running in the background.',
          callback: _startForegroundCallback,
        );
      }
      // start/restartService() always resets the fixed text above, even for a
      // service that was already running muted -- e.g. every normal app open
      // via pages/home/page.dart's postFrameCallback, not just this file's own
      // "newly granted location permission" caller. Applying the persisted
      // mute here, once, covers every caller instead of relying on each one to
      // remember it (see the SharedPreferencesUtil().deviceMuted restore in
      // CaptureController's constructor for why this pref is the source of truth).
      if (SharedPreferencesUtil().deviceMuted) await updateMuteState(true);
      Logger.debug('ForegroundTask started successfully');
      return result;
    } catch (e) {
      Logger.debug('ForegroundTask start failed: $e');
      return ServiceRequestFailure(error: e.toString());
    } finally {
      _isStarting = false;
    }
  }

  /// Update the persistent foreground-service notification's body text to
  /// reflect mute state, so double-tap mute/unmute (which never touches the
  /// device's own LED — see omi/firmware) has *some* always-visible signal
  /// besides the easy-to-miss in-app "Paused" indicator. A no-op if the
  /// foreground service isn't running (e.g. app not yet capturing).
  static Future<void> updateMuteState(bool muted) async {
    try {
      if (!await FlutterForegroundTask.isRunningService) return;
      await FlutterForegroundTask.updateService(
        notificationText:
            muted ? 'Muted — not recording.' : 'Transcription service is running in the background.',
      );
    } catch (e) {
      Logger.debug('ForegroundTask updateMuteState failed: $e');
    }
  }

  static Future<void> stopForegroundTask() async {
    Logger.debug('stopForegroundTask');

    try {
      if (await FlutterForegroundTask.isRunningService) {
        await FlutterForegroundTask.stopService();
        _isInitialized = false;
      }
    } catch (e) {
      Logger.debug('ForegroundTask stop failed: $e');
    }
  }
}
