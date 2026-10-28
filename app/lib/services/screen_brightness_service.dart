import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'package:omi/utils/logger.dart';

/// The phone's screen brightness as a percentage (Android only; see
/// android/.../widgets/ScreenBrightnessPlugin.kt). Writing it needs the "Modify system settings"
/// permission, which only the user can grant on the OS page [requestPermission] opens.
class ScreenBrightnessService {
  ScreenBrightnessService._() : _bridge = _platformBridge;

  @visibleForTesting
  ScreenBrightnessService.testing(this._bridge);

  static final ScreenBrightnessService instance = ScreenBrightnessService._();

  static const MethodChannel _channel = MethodChannel('com.omi/screen_brightness');

  final Future<Object?> Function(String method, Map<String, Object?> arguments) _bridge;

  static Future<Object?> _platformBridge(String method, Map<String, Object?> arguments) async {
    if (!Platform.isAndroid) return null;
    return _channel.invokeMethod<Object?>(method, arguments);
  }

  /// Whether this phone can have its brightness set at all.
  static bool get supported => Platform.isAndroid;

  /// Whether the user has granted "Modify system settings".
  Future<bool> canWrite() async {
    try {
      return await _bridge('canWrite', const {}) == true;
    } catch (e) {
      Logger.debug('ScreenBrightnessService.canWrite failed: $e');
      return false;
    }
  }

  /// Opens the OS page where the user grants the permission; false when it could not open.
  Future<bool> requestPermission() async {
    try {
      return await _bridge('requestPermission', const {}) == true;
    } catch (e) {
      Logger.debug('ScreenBrightnessService.requestPermission failed: $e');
      return false;
    }
  }

  /// The current level, 0..100, or null when it cannot be read.
  Future<int?> current() async {
    try {
      return (await _bridge('get', const {}) as num?)?.toInt();
    } catch (e) {
      Logger.debug('ScreenBrightnessService.current failed: $e');
      return null;
    }
  }

  /// Sets the level ([percent] 1..100, in manual mode). False when the permission is missing or
  /// the write failed, so the caller can say so instead of assuming the screen changed.
  Future<bool> set(int percent) async {
    try {
      return await _bridge('set', {'percent': percent.clamp(1, 100)}) == true;
    } on PlatformException catch (e) {
      if (e.code != 'permission') Logger.debug('ScreenBrightnessService.set failed: $e');
      return false;
    } catch (e) {
      Logger.debug('ScreenBrightnessService.set failed: $e');
      return false;
    }
  }
}
