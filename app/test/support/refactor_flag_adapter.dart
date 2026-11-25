import 'package:omi/services/experiments/client_refactor_flags.dart';

/// Switches upstream's client refactor paths for a test. Keys are the upstream cohort names; any
/// key not given is off, the self-hosted default.
class RefactorFlagAdapter {
  RefactorFlagAdapter(this.flags);
  final Map<String, bool> flags;
}

Future<RefactorFlagAdapter> setRefactorFlags(Map<String, bool> flags) async {
  ClientRefactorFlags.debugServing = flags['clients_refactor'] ?? false;
  ClientRefactorFlags.debugChunkUploads = flags['clients_from_segments_chunks'] ?? false;
  ClientRefactorFlags.debugCustodyRelease = flags['custody_capture_committed_release'] ?? false;
  await Future<void>.delayed(Duration.zero);
  return RefactorFlagAdapter(flags);
}

/// Back to the self-hosted defaults (every refactor path off).
void resetRefactorFlags() {
  ClientRefactorFlags.debugServing = false;
  ClientRefactorFlags.debugChunkUploads = false;
  ClientRefactorFlags.debugCustodyRelease = false;
}
