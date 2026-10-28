import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/services/mute_brightness_link.dart';
import 'package:omi/services/screen_brightness_service.dart';

/// Settings > Mute & screen: the screen follows the capture mute, only on a change of the mute,
/// only while the feature is on (and only on Android, which the service decides).
void main() {
  late List<(String, Map<String, Object?>)> calls;
  late ScreenBrightnessService brightness;
  late ValueNotifier<bool> muted;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      'muteBrightnessEnabled': true,
      'mutedBrightnessPercent': 7,
      'unmutedBrightnessPercent': 55,
    });
    await SharedPreferencesUtil.init();
    calls = [];
    brightness = ScreenBrightnessService.testing((method, args) async {
      calls.add((method, args));
      return true;
    });
    muted = ValueNotifier(false);
  });

  MuteBrightnessLink link() =>
      MuteBrightnessLink(muteSource: muted, isMuted: () => muted.value, brightness: brightness);

  test('the levels come from the settings, clamped to 1..100', () {
    final l = link();
    expect(l.levelFor(true), 7);
    expect(l.levelFor(false), 55);
    SharedPreferencesUtil().mutedBrightnessPercent = 0;
    expect(SharedPreferencesUtil().mutedBrightnessPercent, 1, reason: 'a muted screen is never fully dark');
  });

  test('a mute change sets the matching level once; a notification without a change does nothing', () async {
    final l = link()..start();
    muted.notifyListeners();
    muted.value = true;
    await Future<void>.delayed(Duration.zero);
    muted.value = false;
    await Future<void>.delayed(Duration.zero);
    l.dispose();
    muted.value = true;
    await Future<void>.delayed(Duration.zero);
    if (!ScreenBrightnessService.supported) {
      expect(calls, isEmpty, reason: 'off Android the screen is never touched');
      expect(await l.applyNow(), false);
      return;
    }
    expect(calls.map((c) => c.$2['percent']), [7, 55], reason: 'muted, then listening; nothing after dispose');
  }, skip: Platform.isAndroid ? false : null);

  test('with the feature off nothing is applied', () async {
    SharedPreferencesUtil().muteBrightnessEnabled = false;
    expect(await link().applyNow(), false);
    expect(calls, isEmpty);
  });
}
