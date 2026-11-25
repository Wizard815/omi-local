import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Where typed chat is answered (Android only; see README.md in this folder).
enum PhoneLlmMode {
  /// Omi's server, as before. The default.
  server('server'),

  /// The model on this phone, every time.
  phone('phone'),

  /// The server, and this phone only when the server cannot be reached at all.
  auto('auto');

  const PhoneLlmMode(this.wire);

  /// The stored value, also the widget document's `mode`.
  final String wire;

  static PhoneLlmMode fromWire(String? value) =>
      PhoneLlmMode.values.firstWhere((m) => m.wire == value, orElse: () => PhoneLlmMode.server);

  /// Server → This phone → Auto → Server (the widget's Switch).
  PhoneLlmMode get next => PhoneLlmMode.values[(index + 1) % PhoneLlmMode.values.length];
}

/// Holds the phone model's API key. Secure storage on the phone; a map in tests.
abstract class PhoneLlmSecretStore {
  Future<String?> read();
  Future<void> write(String? value);
}

class _SecureSecretStore implements PhoneLlmSecretStore {
  static const _key = 'phone_llm_api_key';
  static const _storage = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));

  @override
  Future<String?> read() => _storage.read(key: _key);

  @override
  Future<void> write(String? value) =>
      value == null || value.isEmpty ? _storage.delete(key: _key) : _storage.write(key: _key, value: value);
}

class MemorySecretStore implements PhoneLlmSecretStore {
  MemorySecretStore([this.value]);
  String? value;

  @override
  Future<String?> read() async => value;

  @override
  Future<void> write(String? value) async => this.value = value;
}

/// The phone AI settings, in their own preference keys (not the shared preferences.dart).
class PhoneLlmSettings extends ChangeNotifier {
  PhoneLlmSettings({PhoneLlmSecretStore? secrets, Future<SharedPreferences> Function()? prefs})
      : _secrets = secrets ?? _SecureSecretStore(),
        _prefs = prefs ?? SharedPreferences.getInstance;

  static PhoneLlmSettings instance = PhoneLlmSettings();

  static const defaultBaseUrl = 'http://127.0.0.1:8081/v1';
  static const _modeKey = 'phone_llm_mode';
  static const _baseUrlKey = 'phone_llm_base_url';
  static const _modelKey = 'phone_llm_model';
  static const _contextKey = 'phone_llm_include_conversations';

  final PhoneLlmSecretStore _secrets;
  final Future<SharedPreferences> Function() _prefs;
  Future<void>? _loading;

  PhoneLlmMode mode = PhoneLlmMode.server;
  String baseUrl = defaultBaseUrl;
  String apiKey = '';
  String model = '';
  bool includeConversations = false;

  /// True once the stored values were read ([ensureLoaded]).
  bool loaded = false;

  /// Whether a phone model was picked; nothing is sent to the phone before that.
  bool get configured => model.isNotEmpty;

  /// Reads the stored values once; later calls return the same future.
  Future<void> ensureLoaded() => _loading ??= _load();

  Future<void> _load() async {
    final prefs = await _prefs();
    mode = PhoneLlmMode.fromWire(prefs.getString(_modeKey));
    baseUrl = prefs.getString(_baseUrlKey) ?? defaultBaseUrl;
    model = prefs.getString(_modelKey) ?? '';
    includeConversations = prefs.getBool(_contextKey) ?? false;
    try {
      apiKey = await _secrets.read() ?? '';
    } catch (_) {
      apiKey = ''; // A keystore that cannot be read leaves the key empty; the user enters it again.
    }
    loaded = true;
    notifyListeners();
  }

  Future<void> setMode(PhoneLlmMode value) async {
    mode = value;
    notifyListeners();
    await (await _prefs()).setString(_modeKey, value.wire);
  }

  Future<PhoneLlmMode> cycleMode() async {
    await ensureLoaded();
    await setMode(mode.next);
    return mode;
  }

  Future<void> setBaseUrl(String value) async {
    final trimmed = value.trim().replaceFirst(RegExp(r'/+$'), '');
    baseUrl = trimmed.isEmpty ? defaultBaseUrl : trimmed;
    notifyListeners();
    await (await _prefs()).setString(_baseUrlKey, baseUrl);
  }

  Future<void> setApiKey(String value) async {
    apiKey = value.trim();
    notifyListeners();
    await _secrets.write(apiKey);
  }

  Future<void> setModel(String value) async {
    model = value;
    notifyListeners();
    await (await _prefs()).setString(_modelKey, value);
  }

  Future<void> setIncludeConversations(bool value) async {
    includeConversations = value;
    notifyListeners();
    await (await _prefs()).setBool(_contextKey, value);
  }

  /// `http://127.0.0.1:8081/v1` → `http://127.0.0.1:8081/health` (llama-server's health check,
  /// which needs no API key).
  Uri get healthUri {
    final base = Uri.parse(baseUrl);
    final segments = base.pathSegments.where((s) => s.isNotEmpty).toList();
    if (segments.isNotEmpty && segments.last == 'v1') segments.removeLast();
    return base.replace(pathSegments: [...segments, 'health']);
  }
}
