import 'package:flutter/foundation.dart';

import 'package:omi/services/phone_llm/phone_llm_router.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';

/// The phone model's last known state (`GET /health`) and when it was checked. Checked only while
/// the app runs (HomeWidgetsPublisher drives it); the AI source widget shows the last result.
class PhoneLlmStatus extends ChangeNotifier {
  PhoneLlmStatus({PhoneLlmSettings? settings, PhoneProbe? probe, DateTime Function()? now})
      : settings = settings ?? PhoneLlmSettings.instance,
        _probe = probe ?? probePhone,
        _now = now ?? DateTime.now;

  final PhoneLlmSettings settings;
  final PhoneProbe _probe;
  final DateTime Function() _now;

  PhoneHealth? health;
  DateTime? checkedAt;
  bool _checking = false;

  Future<void> check() async {
    if (_checking) return;
    _checking = true;
    try {
      await settings.ensureLoaded();
      health = await _probe(settings.healthUri);
      checkedAt = _now();
      notifyListeners();
    } finally {
      _checking = false;
    }
  }
}
