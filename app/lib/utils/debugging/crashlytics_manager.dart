import 'package:flutter/material.dart';

import 'package:omi/utils/debugging/non_fatal_reporter.dart';

/// No-op stub. This app is self-hosted with zero Google/cloud crash
/// reporting — every method here used to call FirebaseCrashlytics, which
/// always talks to real Google servers with no local/emulator equivalent
/// and no way to point it at a self-hosted endpoint. The class keeps its
/// original public API (rather than being deleted) so every existing call
/// site (main.dart's FlutterError.onError/PlatformDispatcher.onError hooks,
/// shared.dart's error logging, etc.) needs no changes — they just log
/// nowhere instead of to Google.
class CrashlyticsManager {
  static final CrashlyticsManager _instance = CrashlyticsManager._internal();
  static CrashlyticsManager get instance => _instance;

  CrashlyticsManager._internal();

  factory CrashlyticsManager() {
    return _instance;
  }

  static Future<void> init() async {}

  /// Upstream attaches git SHA / build number to Crashlytics here; there is
  /// no Crashlytics in a self-hosted build, so this is a no-op.
  static Future<void> applyBuildProvenanceKeys() async {}

  void identifyUser(String email, String name, String userId) {}

  void logInfo(String message) {}

  void logError(String message) {}

  void logWarn(String message) {}

  void logDebug(String message) {}

  void logVerbose(String message) {}

  void setUserAttribute(String key, String value) {}

  void setEnabled(bool isEnabled) {}

  /// Where reports go instead of Crashlytics. Nothing leaves the device: the
  /// default drops them. Tests replace it to check that a failure is reported
  /// exactly once, and that a failing sink cannot escape as an uncaught error.
  @visibleForTesting
  static Future<void> Function(Object error, StackTrace stack, {required bool fatal}) localRecord = _dropReport;

  static Future<void> _dropReport(Object error, StackTrace stack, {required bool fatal}) async {}

  static Future<void> _ignoreAttribute(String key, String value) async {}

  Future<void> reportCrash(
    Object exception,
    StackTrace stackTrace, {
    Map<String, String>? userAttributes,
  }) =>
      recordNonFatalFailure(exception, stackTrace, record: localRecord, setAttribute: _ignoreAttribute);

  Future<void> reportNonFatal(
    Object exception,
    StackTrace stackTrace, {
    Map<String, String> userAttributes = const {},
  }) =>
      recordNonFatalFailure(exception, stackTrace, record: localRecord, setAttribute: _ignoreAttribute);

  NavigatorObserver? getNavigatorObserver() {
    return null;
  }

  bool get isSupported => false;
}
