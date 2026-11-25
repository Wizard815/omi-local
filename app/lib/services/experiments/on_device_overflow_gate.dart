import 'package:flutter/foundation.dart';

/// Client presentation gate for on-device transcription once live hours run out (the at-cap
/// choice sheet, model setup, schedule, queue banner, provenance and settings).
///
/// A self-hosted server has no live-hour cap, so the gate is off: every existing surface stays as
/// it is. There is no remote flag; only a debug build can switch it on.
abstract final class OnDeviceOverflowGate {
  /// Debug-build override: `--dart-define=OMI_ON_DEVICE_OVERFLOW=true`.
  static const _debugForce = bool.fromEnvironment('OMI_ON_DEVICE_OVERFLOW');

  static bool get debugOverrideActive => kDebugMode && _debugForce;

  static Future<bool> read() async => debugOverrideActive;
}
