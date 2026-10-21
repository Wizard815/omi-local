import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/env/env.dart';
import 'package:omi/services/auth/auth_token_result.dart';
import 'package:omi/services/auth_service.dart';

/// omi-local: a self-hosted remote-login session (username/password, no Firebase) never has a
/// gateway user. Upstream's session snapshots read that as "signed out", so every snapshot-gated
/// call (data export, on-demand recaps, onboarding sync, language sync) silently did nothing.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    Env.init(_TestEnvFields());
    SharedPreferences.setMockInitialValues({});
    await SharedPreferencesUtil.init();
  });

  String sessionToken(DateTime expiry) {
    String part(Map<String, Object> json) => base64Url.encode(utf8.encode(jsonEncode(json))).replaceAll('=', '');
    return '${part({'alg': 'HS256', 'typ': 'JWT'})}.${part({
          'sub': 'remote-uid',
          'exp': expiry.millisecondsSinceEpoch ~/ 1000
        })}.sig';
  }

  test('a valid remote-login session yields a current snapshot for its own uid', () async {
    final service = AuthService.forTesting(tokenGateway: _NoUserGateway(), refreshDelay: (_) async {});
    await service.establishRemoteSession('remote-uid', sessionToken(DateTime.now().add(const Duration(hours: 1))));

    final snapshot = service.captureSessionSnapshot();

    expect(snapshot?.ownerUid, 'remote-uid');
    expect(service.isSessionSnapshotCurrent(snapshot!), isTrue);
    expect(service.captureSessionSnapshot(expectedUid: 'someone-else'), isNull);
  });

  test('an expired remote-login session yields no snapshot', () async {
    final service = AuthService.forTesting(tokenGateway: _NoUserGateway(), refreshDelay: (_) async {});
    await service.establishRemoteSession(
        'remote-uid', sessionToken(DateTime.now().subtract(const Duration(minutes: 1))));

    expect(service.captureSessionSnapshot(), isNull);
  });
}

final class _NoUserGateway implements AuthTokenGateway {
  @override
  AuthUserSnapshot? get currentUser => null;

  @override
  Future<RefreshedAuthToken?> forceRefresh() async => null;

  @override
  Future<void> signOut() async {}
}

class _TestEnvFields implements EnvFields {
  @override
  String? get apiBaseUrl => 'https://unit-test.invalid/';

  @override
  String? get googleClientId => null;

  @override
  String? get googleClientSecret => null;

  @override
  String? get intercomAppId => null;

  @override
  String? get intercomIOSApiKey => null;

  @override
  String? get intercomAndroidApiKey => null;

  @override
  String? get posthogApiKey => null;

  @override
  bool? get useAuthCustomToken => false;

  @override
  bool? get useWebAuth => false;
}
