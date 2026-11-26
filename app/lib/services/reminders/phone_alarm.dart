import 'dart:io';

import 'package:flutter/services.dart';

/// A real alarm or timer in the phone's clock app (Android `AlarmClock.ACTION_SET_ALARM` /
/// `ACTION_SET_TIMER`, android/app/src/main/kotlin/com/friend/ios/alarms/PhoneAlarmPlugin.kt).
/// The clock app opens with the values filled in and the user saves it there, so nothing is set
/// behind their back. iOS has no such API: [isSupported] is false and every call says so.
class PhoneAlarm {
  PhoneAlarm({MethodChannel? channel, bool? supported})
      : _channel = channel ?? const MethodChannel('com.omi/phone_alarm'),
        isSupported = supported ?? Platform.isAndroid;

  static final PhoneAlarm instance = PhoneAlarm();

  final MethodChannel _channel;
  final bool isSupported;

  static const maxTimerSeconds = 24 * 60 * 60;
  static const maxLabelLength = 120;

  /// The wall-clock time an alarm for [due] rings at, or null when [due] is not ahead of [now]
  /// within a day (the clock app's alarm has a time but no date).
  static ({int hour, int minute})? alarmTimeFor(DateTime due, {required DateTime now}) {
    final local = due.toLocal();
    if (!local.isAfter(now) || local.difference(now) > const Duration(hours: 24)) return null;
    return (hour: local.hour, minute: local.minute);
  }

  static String _label(String? label) {
    final text = (label ?? '').trim().replaceAll(RegExp(r'\s+'), ' ');
    return text.length > maxLabelLength ? '${text.substring(0, maxLabelLength - 1)}…' : text;
  }

  /// Validates and opens the clock app on a new alarm. Every result has `ok`; failures a `reason`.
  Future<Map<String, Object?>> setAlarm({required int hour, required int minute, String? label}) async {
    if (!isSupported) return _failure('unsupported_platform', 'This phone has no clock app Omi can open.');
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
      return _failure('invalid_time', 'hour must be 0-23 and minute 0-59.');
    }
    return _invoke('setAlarm', {'hour': hour, 'minute': minute, 'message': _label(label)});
  }

  /// Opens the clock app on a timer of [seconds] (1 s to 24 h).
  Future<Map<String, Object?>> setTimer({required int seconds, String? label}) async {
    if (!isSupported) return _failure('unsupported_platform', 'This phone has no clock app Omi can open.');
    if (seconds < 1 || seconds > maxTimerSeconds) {
      return _failure('invalid_length', 'seconds must be between 1 and $maxTimerSeconds.');
    }
    return _invoke('setTimer', {'seconds': seconds, 'message': _label(label)});
  }

  Future<Map<String, Object?>> _invoke(String method, Map<String, Object?> args) async {
    try {
      final raw = await _channel.invokeMethod<Object?>(method, args);
      if (raw is Map) return Map<String, Object?>.from(raw);
      return {'ok': true, 'status': 'opened'};
    } on PlatformException catch (e) {
      return _failure(e.code == 'no_clock_app' ? 'no_clock_app' : 'platform_error', e.message ?? e.code);
    } on MissingPluginException {
      return _failure('surface_unavailable', 'The clock bridge is not registered in this build.');
    }
  }

  static Map<String, Object?> _failure(String reason, String message) =>
      {'ok': false, 'reason': reason, 'error': message};
}
