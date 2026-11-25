import 'package:flutter/foundation.dart';

import 'package:omi/utils/logger.dart';

/// Upstream's client refactor cohort switches. A self-hosted build has no remote flag service, so
/// each one keeps the old, proven serving behavior. Tests can switch a path on to keep it covered.
abstract final class ClientRefactorFlags {
  @visibleForTesting
  static bool debugServing = false;
  @visibleForTesting
  static bool debugChunkUploads = false;
  @visibleForTesting
  static bool debugCustodyRelease = false;

  static bool get serving => debugServing;
  static Future<bool> chunkUploads() async => debugChunkUploads;
  // Data release is an opt-in, never a default-on kill switch.
  static Future<bool> custodyRelease() async => debugCustodyRelease;

  static void compare(String check, bool agrees) {
    if (!agrees) Logger.debug('event=refactor_shadow lane=L5 check=$check outcome=disagree');
  }
}
