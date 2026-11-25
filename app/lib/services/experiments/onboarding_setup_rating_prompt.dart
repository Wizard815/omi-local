import 'package:flutter/foundation.dart';

/// Gate for the onboarding "Setting up your Omi" page and its store-rating pre-prompt.
///
/// Off on a self-hosted build: there is no remote flag, and the onboarding flow is exactly what it
/// was before this page existed. Only a debug build can show it.
abstract final class OnboardingSetupRatingPromptGate {
  /// Local override for debug builds only: `--dart-define=OMI_ONBOARDING_SETUP_RATING_PROMPT=1`.
  /// A dart-define cannot be flipped on a built artifact and `kDebugMode` excludes every
  /// store build, so this never reaches a user.
  static const _debugOverride = String.fromEnvironment('OMI_ONBOARDING_SETUP_RATING_PROMPT');

  static bool get debugOverrideActive => kDebugMode && _debugOverride == '1';

  /// Whether to show the page.
  static Future<bool> isEnabled() async => debugOverrideActive;
}
