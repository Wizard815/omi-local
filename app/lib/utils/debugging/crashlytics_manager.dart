import 'package:flutter/material.dart';

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

  void identifyUser(String email, String name, String userId) {}

  void logInfo(String message) {}

  void logError(String message) {}

  void logWarn(String message) {}

  void logDebug(String message) {}

  void logVerbose(String message) {}

  void setUserAttribute(String key, String value) {}

  void setEnabled(bool isEnabled) {}

  Future<void> reportCrash(
    Object exception,
    StackTrace stackTrace, {
    Map<String, String>? userAttributes,
  }) async {}

  NavigatorObserver? getNavigatorObserver() {
    return null;
  }

  bool get isSupported => false;
}
