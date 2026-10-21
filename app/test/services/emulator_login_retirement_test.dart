import 'package:flutter_test/flutter_test.dart';

import 'package:omi/services/auth/emulator_login_retirement.dart';

/// A session from the old LAN login (a `<name>@local.omi` Firebase emulator
/// user) can never authenticate against a server that refuses unsigned
/// emulator tokens. Startup must sign it out — after pointing the SDK at the
/// user's own server — and leave every other session alone.
void main() {
  late List<String> calls;

  Future<bool> retire(String? email, {String savedServerUrl = 'http://192.168.20.5:8000/'}) {
    return retireEmulatorLoginSession(
      currentUserEmail: email,
      savedServerUrl: savedServerUrl,
      pointAtServer: (host) async => calls.add('point:$host'),
      signOut: () async => calls.add('signOut'),
    );
  }

  setUp(() => calls = []);

  test('an old emulator login is pointed at the saved server, then signed out', () async {
    expect(await retire('blake@local.omi'), isTrue);
    expect(calls, ['point:192.168.20.5', 'signOut']);
  });

  test('without a saved server it is still signed out', () async {
    expect(await retire('blake@local.omi', savedServerUrl: ''), isTrue);
    expect(calls, ['signOut']);
  });

  test('no Firebase user (the server-signed session) is left alone', () async {
    expect(await retire(null), isFalse);
    expect(calls, isEmpty);
  });

  test('a Firebase user from any other sign-in is left alone', () async {
    expect(await retire('someone@example.com'), isFalse);
    expect(calls, isEmpty);
  });
}
