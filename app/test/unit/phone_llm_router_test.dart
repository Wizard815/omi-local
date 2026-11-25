import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/services/phone_llm/phone_llm_router.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';

/// When chat goes to this phone instead of the Omi server. A slow server never switches.
void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  Future<PhoneLlmSettings> settings(PhoneLlmMode mode, {String model = 'small-q4'}) async {
    final s = PhoneLlmSettings(secrets: MemorySecretStore());
    await s.ensureLoaded();
    await s.setMode(mode);
    await s.setModel(model);
    return s;
  }

  group('every combination of mode × server × phone', () {
    for (final mode in PhoneLlmMode.values) {
      for (final server in ServerReach.values) {
        for (final phone in PhoneHealth.values) {
          final expected = switch (mode) {
            PhoneLlmMode.server => false,
            PhoneLlmMode.phone => true,
            PhoneLlmMode.auto => server == ServerReach.down && phone == PhoneHealth.running,
          };
          test('${mode.name} · server ${server.name} · phone ${phone.name} → ${expected ? 'phone' : 'server'}',
              () async {
            var serverProbes = 0;
            final router = PhoneLlmRouter(
              settings: await settings(mode),
              server: () async {
                serverProbes++;
                return server;
              },
              phone: (_) async => phone,
            );
            expect(await router.usePhone(), expected);
            if (mode != PhoneLlmMode.auto) expect(serverProbes, 0, reason: 'only Auto looks at the server');
          });
        }
      }
    }
  });

  test('a slow server never switches, even with the phone model running', () async {
    final router = PhoneLlmRouter(
      settings: await settings(PhoneLlmMode.auto),
      server: () async => ServerReach.slow,
      phone: (_) async => PhoneHealth.running,
    );
    expect(await router.usePhone(), isFalse);
  });

  test('nothing goes to the phone before a model is picked, or off Android', () async {
    final unpicked = PhoneLlmRouter(
      settings: await settings(PhoneLlmMode.phone, model: ''),
      server: () async => ServerReach.down,
      phone: (_) async => PhoneHealth.running,
    );
    expect(await unpicked.usePhone(), isFalse);
    expect(unpicked.canAskPhone, isFalse);

    final ios = PhoneLlmRouter(settings: await settings(PhoneLlmMode.phone), isAndroid: false);
    expect(await ios.usePhone(), isFalse);
    expect(ios.canAskPhone, isFalse);
  });

  group('server probe', () {
    test('any HTTP answer is up', () async {
      final c = MockClient((_) async => http.Response('not found', 404));
      expect(await probeServer(Uri.parse('https://omi.example/'), client: c), ServerReach.up);
    });

    test('a timeout is slow, never down', () async {
      final c = MockClient((_) => Completer<http.Response>().future);
      expect(
        await probeServer(Uri.parse('https://omi.example/'), client: c, timeout: const Duration(milliseconds: 20)),
        ServerReach.slow,
      );
    });

    test('refused, DNS failure and no network are down', () async {
      for (final error in [
        const SocketException('Connection refused', osError: OSError('Connection refused', 111)),
        const SocketException('Failed host lookup: \'omi.example\''),
        const SocketException('Network is unreachable', osError: OSError('Network is unreachable', 101)),
      ]) {
        final c = MockClient((_) async => throw error);
        expect(await probeServer(Uri.parse('https://omi.example/'), client: c), ServerReach.down, reason: '$error');
      }
    });

    test('a reset or TLS failure after connecting is not "unreachable"', () async {
      expect(isUnreachableError(const SocketException('Connection reset by peer', osError: OSError('', 104))), isFalse);
      expect(isUnreachableError(const HandshakeException('bad certificate')), isFalse);
      expect(isUnreachableError(TimeoutException('slow')), isFalse);
    });
  });

  test('phone probe: 200 running, 503 loading, anything else off', () async {
    Future<PhoneHealth> probe(Future<http.Response> Function() answer) =>
        probePhone(Uri.parse('http://127.0.0.1:8081/health'), client: MockClient((_) => answer()));
    expect(await probe(() async => http.Response('{"status":"ok"}', 200)), PhoneHealth.running);
    expect(await probe(() async => http.Response('{"status":"loading model"}', 503)), PhoneHealth.loading);
    expect(await probe(() async => http.Response('', 500)), PhoneHealth.off);
    expect(await probe(() async => throw const SocketException('Connection refused')), PhoneHealth.off);
  });

  group('settings', () {
    test('defaults, own keys, the API key in the secret store, and the health address', () async {
      final secrets = MemorySecretStore();
      final s = PhoneLlmSettings(secrets: secrets);
      await s.ensureLoaded();
      expect(s.mode, PhoneLlmMode.server);
      expect(s.baseUrl, 'http://127.0.0.1:8081/v1');
      expect(s.healthUri.toString(), 'http://127.0.0.1:8081/health');
      expect(s.configured, isFalse);

      await s.setApiKey(' k-1 ');
      await s.setBaseUrl('http://127.0.0.1:9000/v1/');
      await s.setIncludeConversations(true);
      expect(secrets.value, 'k-1');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getKeys(), isNot(contains(contains('api_key'))), reason: 'the key is never in plain preferences');

      final again = PhoneLlmSettings(secrets: secrets);
      await again.ensureLoaded();
      expect(again.apiKey, 'k-1');
      expect(again.baseUrl, 'http://127.0.0.1:9000/v1');
      expect(again.includeConversations, isTrue);
    });

    test('Switch cycles Server → This phone → Auto → Server', () async {
      final s = PhoneLlmSettings(secrets: MemorySecretStore());
      expect(await s.cycleMode(), PhoneLlmMode.phone);
      expect(await s.cycleMode(), PhoneLlmMode.auto);
      expect(await s.cycleMode(), PhoneLlmMode.server);
      expect(PhoneLlmMode.fromWire('nonsense'), PhoneLlmMode.server);
    });
  });
}
