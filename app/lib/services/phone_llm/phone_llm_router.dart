import 'dart:async';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'package:omi/env/env.dart';
import 'package:omi/services/phone_llm/phone_llm_client.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';

/// What a short look at the Omi server found.
enum ServerReach {
  /// Something answered (any HTTP status).
  up,

  /// Connection refused, no DNS answer, or no network: the server cannot be reached at all.
  down,

  /// No answer within the probe's time. A slow server is still the server: never switch for this.
  slow,
}

/// What `GET {phone}/health` answered: 200 running, 503 loading the model, otherwise off.
enum PhoneHealth { running, loading, off }

typedef ServerProbe = Future<ServerReach> Function();
typedef PhoneProbe = Future<PhoneHealth> Function(Uri healthUri);

/// Whether a failed connection means "cannot be reached" (switch) rather than "slow" or "broken".
/// Refused, DNS failure, no route or no network do; a reset or a TLS error after connecting
/// does not (the server is there).
bool isUnreachableError(Object error) {
  if (error is! SocketException) return false;
  final code = error.osError?.errorCode;
  // ECONNREFUSED, ENETUNREACH, EHOSTUNREACH on Linux/Android; WSA equivalents elsewhere.
  const codes = {111, 101, 113, 61, 51, 65, 10061, 10051, 10065};
  if (code != null && codes.contains(code)) return true;
  final text = '${error.message} ${error.osError?.message ?? ''}'.toLowerCase();
  return text.contains('failed host lookup') ||
      text.contains('connection refused') ||
      text.contains('network is unreachable') ||
      text.contains('no address associated');
}

/// Probes [base] for at most [timeout]: [ServerReach.slow] on a timeout, never [ServerReach.down].
Future<ServerReach> probeServer(Uri base,
    {Duration timeout = const Duration(milliseconds: 2500), http.Client? client}) async {
  final c = client ?? http.Client();
  try {
    await c.get(base).timeout(timeout);
    return ServerReach.up;
  } on TimeoutException {
    return ServerReach.slow;
  } catch (e) {
    return isUnreachableError(e) ? ServerReach.down : ServerReach.up;
  } finally {
    if (client == null) c.close();
  }
}

Future<PhoneHealth> probePhone(Uri health,
    {Duration timeout = const Duration(milliseconds: 1500), http.Client? client}) async {
  final c = client ?? http.Client();
  try {
    final response = await c.get(health).timeout(timeout);
    return switch (response.statusCode) {
      200 => PhoneHealth.running,
      503 => PhoneHealth.loading,
      _ => PhoneHealth.off,
    };
  } catch (_) {
    return PhoneHealth.off;
  } finally {
    if (client == null) c.close();
  }
}

/// Decides, per typed message, whether this phone answers instead of the Omi server.
///
/// - [PhoneLlmMode.server]: always the server.
/// - [PhoneLlmMode.phone]: always the phone (a stopped phone model then says so in the reply).
/// - [PhoneLlmMode.auto]: the phone only when the server cannot be reached at all *and* the phone
///   model is running. A slow server keeps the turn: it may legitimately take minutes.
///
/// Nothing goes to the phone before a phone model is picked, and never off Android.
class PhoneLlmRouter {
  PhoneLlmRouter({
    PhoneLlmSettings? settings,
    ServerProbe? server,
    PhoneProbe? phone,
    PhoneLlmClient Function(PhoneLlmSettings settings)? clientFor,
    this.isAndroid = true,
  })  : settings = settings ?? PhoneLlmSettings.instance,
        _server = server ?? _defaultServerProbe,
        _phone = phone ?? probePhone,
        _clientFor = clientFor ?? _defaultClient;

  final PhoneLlmSettings settings;
  final ServerProbe _server;
  final PhoneProbe _phone;
  final PhoneLlmClient Function(PhoneLlmSettings) _clientFor;
  final bool isAndroid;

  static Future<ServerReach> _defaultServerProbe() async {
    final base = Uri.tryParse(Env.apiBaseUrl ?? '');
    if (base == null || base.host.isEmpty) return ServerReach.up;
    return probeServer(base);
  }

  static PhoneLlmClient _defaultClient(PhoneLlmSettings s) => PhoneLlmClient(baseUrl: s.baseUrl, apiKey: s.apiKey);

  /// Whether "Ask this phone instead" can be offered (a phone model is set up).
  bool get canAskPhone => isAndroid && settings.configured;

  /// True when this turn goes to the phone.
  Future<bool> usePhone() async {
    if (!isAndroid) return false;
    await settings.ensureLoaded();
    if (!settings.configured) return false;
    switch (settings.mode) {
      case PhoneLlmMode.server:
        return false;
      case PhoneLlmMode.phone:
        return true;
      case PhoneLlmMode.auto:
        if (await _server() != ServerReach.down) return false;
        return await _phone(settings.healthUri) == PhoneHealth.running;
    }
  }

  PhoneLlmClient client() => _clientFor(settings);
}
