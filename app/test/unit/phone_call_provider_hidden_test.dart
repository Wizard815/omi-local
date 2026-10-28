import 'package:flutter_test/flutter_test.dart';

import 'package:omi/pages/phone_calls/phone_calls_feature.dart';
import 'package:omi/providers/phone_call_provider.dart';

/// The self-hosted APK ships without the native Twilio plugin: while the phone-calls feature is
/// hidden, constructing the provider (Home watches it) must not open the native channels or fetch.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('a hidden phone-calls feature wires nothing at construction', () async {
    final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    final touched = <String>[];
    for (final name in ['com.omi/phone_calls', 'com.omi/phone_calls/events']) {
      messenger.setMockMessageHandler(name, (message) async {
        touched.add(name);
        return null;
      });
    }
    addTearDown(() {
      for (final name in ['com.omi/phone_calls', 'com.omi/phone_calls/events']) {
        messenger.setMockMessageHandler(name, null);
      }
    });
    expect(PhoneCallsFeature.visible, false, reason: 'this guard only matters while the feature is hidden');
    final provider = PhoneCallProvider();
    await provider.initialLoad;
    await Future<void>.delayed(Duration.zero);
    expect(touched, isEmpty);
  });
}
