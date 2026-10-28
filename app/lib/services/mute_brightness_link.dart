import 'dart:async';

import 'package:flutter/foundation.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/services/screen_brightness_service.dart';

/// Ties the phone's screen brightness to the capture mute (Settings > Mute & screen): each time
/// capture goes muted the screen is set to the muted level, and each time it listens again to the
/// normal level. Only changes of the mute move the screen, so the user's own brightness changes
/// in between are left alone. [muteSource] notifies on capture changes; [isMuted] reads the mute.
class MuteBrightnessLink {
  MuteBrightnessLink({
    required this.muteSource,
    required this.isMuted,
    SharedPreferencesUtil? prefs,
    ScreenBrightnessService? brightness,
  })  : _prefs = prefs ?? SharedPreferencesUtil(),
        _brightness = brightness ?? ScreenBrightnessService.instance;

  final Listenable muteSource;
  final bool Function() isMuted;
  final SharedPreferencesUtil _prefs;
  final ScreenBrightnessService _brightness;
  bool? _lastMuted;

  void start() {
    _lastMuted = isMuted();
    muteSource.addListener(_onChange);
  }

  void dispose() {
    muteSource.removeListener(_onChange);
  }

  void _onChange() {
    final muted = isMuted();
    if (muted == _lastMuted) return;
    _lastMuted = muted;
    unawaited(apply(muted));
  }

  /// The level the screen gets while [muted].
  int levelFor(bool muted) => muted ? _prefs.mutedBrightnessPercent : _prefs.unmutedBrightnessPercent;

  /// Sets the screen for the given mute state now, when the feature is on. True when the screen
  /// was set; false when the feature is off, unsupported, or the permission is missing.
  Future<bool> apply(bool muted) async {
    if (!ScreenBrightnessService.supported || !_prefs.muteBrightnessEnabled) return false;
    return _brightness.set(levelFor(muted));
  }

  /// Settings changed: put the screen at the level for the current mute state.
  Future<bool> applyNow() => apply(isMuted());
}
