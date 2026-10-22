import 'dart:async';
import 'package:omi/services/proactivity/proactivity_runtime.dart';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:url_launcher/url_launcher.dart';

import 'package:omi/backend/http/api/apps.dart' as apps_api;
import 'package:omi/backend/http/api/notifications.dart';
import 'package:omi/backend/preferences.dart';
import 'package:omi/env/env.dart';
import 'package:omi/env/environment_profile.dart';
import 'package:omi/app_globals.dart';
import 'package:omi/providers/base_provider.dart';
import 'package:omi/services/account_cutover/account_cutover_runtime.dart';
import 'package:omi/services/auth_service.dart';
import 'package:omi/services/siri_integration.dart';
import 'package:omi/services/auth/auth_token_result.dart';
import 'package:omi/services/notifications.dart';
import 'package:omi/utils/auth/clear_user_state.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/logger.dart';
import 'package:omi/utils/platform/platform_manager.dart';

/// Runs a provider-link helper, then migrates only the anonymous source proof
/// it returned after the destination account has been established.
Future<ProviderLinkResult?> completeProviderLinkAndMigrate({
  required Future<ProviderLinkResult?> Function() linkProvider,
  required Future<bool> Function(String sourceUid, String sourceToken) migrate,
}) async {
  final result = await linkProvider();
  final source = result?.anonymousSourceMigration;
  final destinationUid = result?.destinationUid;
  if (source != null && destinationUid != null && destinationUid != source.uid) {
    await migrate(source.uid, source.token);
  }
  return result;
}

class AuthenticationProvider extends BaseProvider {
  FirebaseAuth get _auth => FirebaseAuth.instance;

  /// A user-typed self-host server URL must end in '/' — every authenticated
  /// request is built as `'${Env.apiBaseUrl}v1/...'`, so a missing slash turns
  /// the host into `hostv1`. Normalized here AND re-normalized in
  /// `Env.apiBaseUrl`, so both the login call and every later request agree.
  static String _normalizeServerUrl(String url) {
    final trimmed = url.trim();
    return trimmed.endsWith('/') ? trimmed : '$trimmed/';
  }

  User? user;
  String? authToken;
  bool _loading = false;
  bool _requiresReauthentication = false;
  AuthSessionExpirationReason? _sessionExpirationReason;
  int _sessionExpirationGeneration = 0;
  StreamSubscription<User?>? _authStateSubscription;
  StreamSubscription<User?>? _idTokenSubscription;
  StreamSubscription<User?>? _siriIdTokenSubscription;
  StreamSubscription<AuthSessionExpiredEvent>? _sessionExpiredSubscription;
  @override
  bool get loading => _loading;
  bool get requiresReauthentication => _requiresReauthentication;

  /// Why the current session expired, while [requiresReauthentication] is true.
  AuthSessionExpirationReason? get sessionExpirationReason => _sessionExpirationReason;
  int get sessionExpirationGeneration => _sessionExpirationGeneration;

  AuthenticationProvider({bool initializeListeners = true}) {
    if (initializeListeners) _initializeAuthListeners();
  }

  void _initializeAuthListeners() {
    // DEBUG: Log initial state
    Logger.debug(
      'DEBUG AuthProvider: Initial currentUser=${_auth.currentUser?.uid}, isAnonymous=${_auth.currentUser?.isAnonymous}',
    );

    Future.microtask(() {
      _authStateSubscription = _auth.authStateChanges().distinct((p, n) => p?.uid == n?.uid).listen((User? user) async {
        PlatformManager.instance.analytics.bindIdentity(user?.uid);
        unawaited(ProactivityRuntime.outbox.bindOwner(user != null && !user.isAnonymous ? user.uid : null));
        AuthService.instance.handleAuthUserChanged(user?.uid);
        Logger.debug(
          'DEBUG AuthProvider: authStateChanges fired - user=${user?.uid}, isAnonymous=${user?.isAnonymous}',
        );
        this.user = user;
        // Only update SharedPreferences if Firebase has a user
        // Don't clear cached credentials - allows fallback for dev builds
        if (user != null) {
          SharedPreferencesUtil().uid = user.uid;
          SharedPreferencesUtil().email = user.email ?? '';
          SharedPreferencesUtil().givenName = user.displayName?.split(' ')[0] ?? '';
        }
        final cutoverOwner = (user != null && !user.isAnonymous) ? user.uid : null;
        unawaited(AccountCutoverRuntime.instance.bindAuthenticatedOwner(cutoverOwner));
        notifyListeners();
        await SiriIntegration.instance.accountChanged(user);
      });
      // Token refreshes are Siri-only. Keep the app's original UID-distinct
      // listener so a token refresh does not replay its auth/UI side effects.
      _siriIdTokenSubscription = _auth.idTokenChanges().listen((User? user) async {
        if (user != null) await SiriIntegration.instance.refreshSession(user);
      });
      _idTokenSubscription = _auth.idTokenChanges().distinct((p, n) => p?.uid == n?.uid).listen((User? user) async {
        PlatformManager.instance.analytics.bindIdentity(user?.uid);
        unawaited(ProactivityRuntime.outbox.bindOwner(user != null && !user.isAnonymous ? user.uid : null));
        AuthService.instance.handleAuthUserChanged(user?.uid);
        if (user == null) {
          // A self-hosted remote-login session (see AuthService.signInWithRemoteAccount)
          // never creates a Firebase user by design — no Firebase/Google is
          // involved at all, only this backend's own signed session token.
          // Firebase's idTokenChanges() therefore correctly reports user=null
          // on every single app start for that session kind, which — before
          // this guard — wiped the valid stored session token here every
          // time, forcing a re-login on every cold start even though the
          // session itself was never expired. isSignedIn() and
          // refreshIdToken() already treat isLocalRemoteSession as
          // authoritative and independent of Firebase; this listener must
          // not fight that by clearing the token it relies on.
          if (SharedPreferencesUtil().isLocalRemoteSession) {
            return;
          }
          Logger.debug('User is currently signed out or the token has been revoked!');
          SharedPreferencesUtil().authToken = '';
          SharedPreferencesUtil().tokenExpirationTime = 0;
          authToken = null;
        } else {
          Logger.debug('User is signed in at ${DateTime.now()} with user ${user.uid}');
          try {
            if (_requiresReauthentication ||
                SharedPreferencesUtil().authToken.isEmpty ||
                DateTime.now().millisecondsSinceEpoch > SharedPreferencesUtil().tokenExpirationTime) {
              authToken = await AuthService.instance.getIdToken();
            }
            if (authToken != null && authToken!.isNotEmpty) {
              _requiresReauthentication = false;
            }
          } catch (e) {
            authToken = null;
            Logger.debug('Failed to get token: $e');
          }
        }
        notifyListeners();
      });
      _sessionExpiredSubscription = AuthService.instance.sessionExpiredEvents.listen((event) async {
        _requiresReauthentication = true;
        _sessionExpirationReason = event.reason;
        _sessionExpirationGeneration++;
        user = null;
        authToken = null;
        final rootContext = globalNavigatorKey.currentContext;
        if (rootContext != null && rootContext.mounted) {
          clearAllUserState(rootContext);
        }
        notifyListeners();
        await SiriIntegration.instance.accountChanged(null);
      });
    });
  }

  bool isSignedIn() {
    if (_requiresReauthentication) return false;
    // Local dev: anonymous Firebase emulator users are valid
    if (Env.profile == AppEnvironmentProfile.localDev && _auth.currentUser != null) {
      return true;
    }
    // AuthService.instance.isSignedIn() also covers a self-hosted remote-login
    // session (see establishRemoteSession) — that path deliberately never
    // touches FirebaseAuth, so checking only _auth.currentUser here (as this
    // method used to) meant a remote session was NEVER recognized as signed
    // in, even with a perfectly valid, persisted session token: every cold
    // start (app relaunch after being killed) bounced straight back to the
    // login screen instead of reading the stored session.
    return AuthService.instance.isSignedIn();
  }

  bool get _hasFirebaseUser => _auth.currentUser != null && !_auth.currentUser!.isAnonymous;

  /// Self-hosted login: server URL + username + password, provisioned once on
  /// the server (backend/scripts/seed_local_account.py) rather than created
  /// in-app. The backend's /v1/auth/local-login checks the password and
  /// returns a session token it signs itself; FirebaseAuth is never touched
  /// (see AuthService.signInWithRemoteAccount / establishRemoteSession). The
  /// same call serves the LAN and a tunnel. There used to be a LAN-only login
  /// that signed in to the server's Firebase Auth emulator, whose unsigned
  /// tokens the server now refuses. Uses [_completeRemoteSignIn] instead of
  /// [_signIn], which requires a real Firebase UserCredential this path never
  /// has.
  Future<void> signInRemoteAccount(String serverUrl, String username, String password, Function() onSignIn) async {
    if (loading) return;
    setLoadingState(true);
    try {
      if (serverUrl.isNotEmpty) {
        final normalized = _normalizeServerUrl(serverUrl);
        SharedPreferencesUtil().customApiBaseUrl = normalized;
        Env.overrideApiBaseUrl(normalized);
      }

      final uid = await AuthService.instance.signInWithRemoteAccount(
        SharedPreferencesUtil().customApiBaseUrl,
        username,
        password,
      );
      if (uid != null) {
        _completeRemoteSignIn(onSignIn);
      } else {
        AppSnackbar.showSnackbarError(
          globalNavigatorKey.currentContext?.l10n.authenticationFailed ?? 'Authentication failed. Please try again.',
        );
      }
    } catch (e) {
      Logger.debug('Remote account sign in error: $e');
      AppSnackbar.showSnackbarError('$e'.replaceFirst('Exception: ', ''));
    }
    setLoadingState(false);
  }

  /// Post-sign-in bookkeeping for the remote-login path — the counterpart to
  /// [_signIn] for a session that has no Firebase UserCredential/User object
  /// at all (AuthService.establishRemoteSession already wrote uid/authToken
  /// to SharedPreferences before this is called). [user] intentionally stays
  /// null for this session kind: any UI reading provider.user for profile
  /// display (name/email/photo) won't have that for a remote-login session,
  /// same as it wouldn't for a token obtained any other non-Firebase way.
  void _completeRemoteSignIn(Function() onSignIn) {
    authToken = SharedPreferencesUtil().authToken;
    _requiresReauthentication = false;
    PlatformManager.instance.analytics.identify(authMethod: 'local_remote', userCreatedAt: null);
    notifyListeners();
    onSignIn();
  }

  @override
  void dispose() {
    _authStateSubscription?.cancel();
    _idTokenSubscription?.cancel();
    _siriIdTokenSubscription?.cancel();
    _sessionExpiredSubscription?.cancel();
    super.dispose();
  }

  void setLoading(bool value) {
    _loading = value;
    notifyListeners();
  }

  /// True only for a local_dev build. Drives whether the dev sign-in affordance
  /// is offered at all; the authoritative gate is server-side.
  bool get isLocalDevProfile => Env.profile == AppEnvironmentProfile.localDev;

  Future<void> onLocalDevSignIn(Function() onSignIn) async {
    if (loading) return;
    setLoadingState(true);
    try {
      final credential = await AuthService.instance.signInWithLocalDevToken();
      if (credential != null && _hasFirebaseUser) {
        await _signIn(onSignIn, credential: credential, authProvider: 'local_dev');
      } else {
        AppSnackbar.showSnackbarError('Local development sign-in did not produce a session.');
      }
    } catch (e) {
      // Surfaced verbatim: this path exists for developers, and the message
      // already says exactly what is wrong (harness down, wrong profile, no
      // emulator). Replacing it with a generic string would hide the diagnosis.
      Logger.debug('Local development sign in error: $e');
      AppSnackbar.showSnackbarError('$e');
    }
    setLoadingState(false);
  }

  Future<String?> _getIdToken() async {
    try {
      final token = await AuthService.instance.getIdToken();
      NotificationService.instance.saveNotificationToken();
      try {
        final timeZone = (await FlutterTimezone.getLocalTimezone()).identifier;
        unawaited(syncUserTimeZoneServer(timeZone: timeZone));
      } catch (e) {
        Logger.debug('Failed to sync device timezone: $e');
      }

      Logger.debug('Firebase token retrieved successfully');
      return token;
    } catch (e, stackTrace) {
      AppSnackbar.showSnackbarError(
        globalNavigatorKey.currentContext?.l10n.authFailedToRetrieveToken ??
            'Failed to retrieve firebase token, please try again.',
      );
      PlatformManager.instance.crashReporter.reportCrash(e, stackTrace);

      return null;
    }
  }

  Future<void> _signIn(Function() onSignIn, {required UserCredential credential, required String authProvider}) async {
    final token = await _getIdToken();

    if (token == null) {
      PlatformManager.instance.analytics.signInFailed(authProvider, error: StateError('token'));
    }
    if (token != null) {
      User currentUser;
      try {
        currentUser = FirebaseAuth.instance.currentUser!;
      } catch (e, stackTrace) {
        AppSnackbar.showSnackbarError(
          globalNavigatorKey.currentContext?.l10n.authUnexpectedErrorFirebase ??
              'Unexpected error signing in, Firebase error, please try again.',
        );

        PlatformManager.instance.crashReporter.reportCrash(e, stackTrace);
        return;
      }
      final newUid = currentUser.uid;
      SharedPreferencesUtil().uid = newUid;
      user = currentUser;
      authToken = token;
      _requiresReauthentication = false;
      PlatformManager.instance.analytics.identify(
        authMethod: authProvider,
        userCreatedAt: currentUser.metadata.creationTime,
      );
      if (credential.additionalUserInfo?.isNewUser == true) {
        PlatformManager.instance.analytics.accountCreated(authProvider: authProvider);
      }
      notifyListeners();
      onSignIn();
    } else {
      AppSnackbar.showSnackbarError(
        globalNavigatorKey.currentContext?.l10n.authUnexpectedError ?? 'Unexpected error signing in, please try again',
      );
    }
  }

  void openTermsOfService() {
    _launchUrl('https://www.omi.me/pages/terms-of-service');
  }

  void openPrivacyPolicy() {
    _launchUrl('https://www.omi.me/pages/privacy');
  }

  void _launchUrl(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) {
      Logger.debug('Invalid URL');
      return;
    }

    await launchUrl(uri, mode: LaunchMode.inAppBrowserView);
  }

  Future<void> linkWithGoogle() async {
    setLoading(true);
    try {
      final result = await completeProviderLinkAndMigrate(
        linkProvider: AuthService.instance.linkWithGoogle,
        migrate: migrateAppOwnerId,
      );
      if (result == null) {
        setLoading(false);
        return;
      }
    } catch (e) {
      AppSnackbar.showSnackbarError(
        globalNavigatorKey.currentContext?.l10n.authFailedToLinkGoogle ??
            'Failed to link with Google, please try again.',
      );
      rethrow;
    } finally {
      setLoading(false);
    }
  }

  Future<void> linkWithApple() async {
    setLoading(true);
    try {
      final appleProvider = AppleAuthProvider();
      try {
        await FirebaseAuth.instance.currentUser?.linkWithProvider(appleProvider);
      } catch (e) {
        if (e is FirebaseAuthException && e.code == 'credential-already-in-use') {
          // Get existing user credentials
          final existingCred = e.credential;
          final oldUserId = FirebaseAuth.instance.currentUser?.uid;
          final sourceToken = await FirebaseAuth.instance.currentUser?.getIdToken();

          // Sign out current anonymous user
          await AuthService.instance.signOutForAccountSwitch();

          // Sign in with existing account
          await FirebaseAuth.instance.signInWithCredential(existingCred!);
          final newUserId = FirebaseAuth.instance.currentUser?.uid;
          if (newUserId != null) AuthService.instance.markAuthenticatedUser(newUserId);
          await AuthService.instance.getIdToken();

          SharedPreferencesUtil().onboardingCompleted = false;
          SharedPreferencesUtil().uid = newUserId ?? '';
          SharedPreferencesUtil().email = FirebaseAuth.instance.currentUser?.email ?? '';
          SharedPreferencesUtil().givenName = FirebaseAuth.instance.currentUser?.displayName?.split(' ')[0] ?? '';
          if (oldUserId != null && newUserId != null && sourceToken != null) {
            await migrateAppOwnerId(oldUserId, sourceToken);
          }
          return;
        }
        AppSnackbar.showSnackbarError(
          globalNavigatorKey.currentContext?.l10n.authFailedToLinkApple ??
              'Failed to link with Apple, please try again.',
        );
        rethrow;
      }
    } catch (e) {
      Logger.debug('Error linking with Apple: $e');
      AppSnackbar.showSnackbarError(
        globalNavigatorKey.currentContext?.l10n.authFailedToLinkApple ?? 'Failed to link with Apple, please try again.',
      );
      rethrow;
    } finally {
      setLoading(false);
    }
  }

  Future<bool> migrateAppOwnerId(String oldId, String sourceToken) async {
    return await apps_api.migrateAppOwnerId(oldId, sourceToken);
  }
}
