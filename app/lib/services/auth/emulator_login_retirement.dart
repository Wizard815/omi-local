/// The app used to have a LAN login that signed in to the server's Firebase
/// Auth emulator directly, as a `<username>@local.omi` emulator user. Those
/// tokens are unsigned, and the server now refuses them
/// (backend/utils/self_hosted_id_tokens.py), so a session restored from that
/// login can never authenticate again. Signing it out at startup sends the
/// user to the login screen, which signs in through the server's
/// /v1/auth/local-login instead.
bool isEmulatorLoginEmail(String? email) => (email ?? '').endsWith('@local.omi');

/// Signs out a restored emulator-login session; returns whether there was one.
///
/// [pointAtServer] runs first with the saved server's host, so any token
/// refresh the Firebase SDK attempts goes to that server, never Google's token
/// service.
Future<bool> retireEmulatorLoginSession({
  required String? currentUserEmail,
  required String savedServerUrl,
  required Future<void> Function(String host) pointAtServer,
  required Future<void> Function() signOut,
}) async {
  if (!isEmulatorLoginEmail(currentUserEmail)) return false;
  final host = Uri.tryParse(savedServerUrl)?.host ?? '';
  if (host.isNotEmpty) await pointAtServer(host);
  await signOut();
  return true;
}
