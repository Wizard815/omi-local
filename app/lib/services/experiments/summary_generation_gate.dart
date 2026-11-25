import 'package:flutter/foundation.dart';

/// Client presentation gate for the conversation-detail summary redesign: the "Not summarized yet"
/// card, the "Summarize with" template sheet, the summary source chips and the inline failure
/// cards. Reliability fixes and the single in-tab progress state ship without it.
///
/// Off on a self-hosted build: there is no remote flag. A debug build or a test can switch it on.
abstract final class SummaryGenerationV2Gate {
  /// Debug builds only: `--dart-define=OMI_SUMMARY_GENERATION_V2=true` forces the redesign on.
  static const _debugForce = bool.fromEnvironment('OMI_SUMMARY_GENERATION_V2');

  static final ValueNotifier<bool> _enabled = ValueNotifier<bool>(kDebugMode && _debugForce);

  /// Tests force the gate with this (reset it in a tearDown); null uses the local default.
  @visibleForTesting
  static bool? debugOverride;

  /// Whether the redesign is on right now.
  static bool get isEnabled => debugOverride ?? _enabled.value;

  /// Notifies if the value changes (it does not at run time; kept for the summary tab's listener).
  static ValueListenable<bool> get listenable => _enabled;

  /// Kept for the detail page, which refreshes on open; there is nothing to fetch.
  static Future<bool> refresh() async => isEnabled;
}
