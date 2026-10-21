import 'dart:io';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import 'package:provider/provider.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/pages/onboarding/widgets/onboarding_card.dart';
import 'package:omi/providers/auth_provider.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

class AuthComponent extends StatefulWidget {
  final VoidCallback onSignIn;

  const AuthComponent({super.key, required this.onSignIn});

  @override
  State<AuthComponent> createState() => _AuthComponentState();
}

class _AuthComponentState extends State<AuthComponent> {
  final _serverUrlController = TextEditingController();
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();

  /// LAN mode talks to the Auth emulator directly (server IP + :9099);
  /// Remote mode goes through /v1/auth/local-login instead, since the
  /// emulator's raw port can't be reached through a Cloudflare-Tunnel-style
  /// HTTPS proxy. See AuthenticationProvider.signInLocalAccount vs
  /// signInRemoteAccount for what actually differs.
  bool _remoteLogin = false;

  @override
  void initState() {
    super.initState();
    // Pre-fill with previously saved server URL
    final saved = SharedPreferencesUtil().customApiBaseUrl;
    if (saved.isNotEmpty) {
      _serverUrlController.text = saved;
    }
  }

  @override
  void dispose() {
    _serverUrlController.dispose();
    _usernameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  bool get _canSubmitLogin => _usernameController.text.trim().isNotEmpty && _passwordController.text.isNotEmpty;

  void _submitLogin(AuthenticationProvider provider) {
    final serverUrl = _serverUrlController.text.trim();
    final username = _usernameController.text.trim();
    final password = _passwordController.text;
    if (_remoteLogin) {
      provider.signInRemoteAccount(serverUrl, username, password, widget.onSignIn);
    } else {
      provider.signInLocalAccount(serverUrl, username, password, widget.onSignIn);
    }
  }

  InputDecoration _fieldDecoration({required String label, required String hint, required IconData icon}) {
    final border = OutlineInputBorder(
      borderRadius: BorderRadius.circular(16),
      borderSide: BorderSide(color: OmiColors.border),
    );
    return InputDecoration(
      hintText: hint,
      hintStyle: TextStyle(color: OmiColors.textTertiary, fontSize: 14),
      labelText: label,
      labelStyle: TextStyle(color: OmiColors.textSecondary, fontSize: 13),
      prefixIcon: Icon(icon, color: OmiColors.textSecondary, size: 20),
      filled: true,
      fillColor: OmiColors.surface2,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: border,
      enabledBorder: border,
      focusedBorder: border.copyWith(borderSide: BorderSide(color: OmiColors.textPrimary)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AuthenticationProvider>(
      builder: (context, provider, child) {
        return OnboardingStep(
          card: OnboardingCard(
            padding: const EdgeInsets.fromLTRB(OmiSpacing.xxl, 26, OmiSpacing.xxl, OmiSpacing.xs),
            content: [
              // Loading indicator or spacing
              SizedBox(
                height: 20,
                child: provider.loading ? const Center(child: OmiSpinner(size: OmiSpinnerSize.small)) : null,
              ),

              // Title text
              Text(
                context.l10n.speakTranscribeSummarize,
                style: TextStyle(
                  color: OmiColors.textPrimary,
                  fontSize: 32,
                  fontWeight: FontWeight.bold,
                  height: 1.2,
                  fontFamily: 'Manrope',
                ),
                textAlign: TextAlign.center,
              ),

              const SizedBox(height: 32),

              // ── Server URL field (local dev only) ──
              if (provider.isLocalDevProfile) ...[
                TextField(
                  controller: _serverUrlController,
                  style: TextStyle(color: OmiColors.textPrimary, fontSize: 14, fontFamily: 'Manrope'),
                  decoration: _fieldDecoration(label: 'Server URL', hint: 'http://192.168.20.5:8000/', icon: Icons.dns),
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: 12),
              ],

              // Sign in buttons
              if (!provider.isLocalDevProfile && (Platform.isIOS || Platform.isAndroid)) ...[
                SizedBox(
                  width: double.infinity,
                  height: 56,
                  child: ElevatedButton(
                    onPressed: () {
                      OmiHaptics.selection();
                      provider.onAppleSignIn(widget.onSignIn);
                    },
                    style: ElevatedButton.styleFrom(
                      backgroundColor: OmiColors.accent,
                      foregroundColor: OmiColors.onAccent,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const FaIcon(FontAwesomeIcons.apple, size: 24),
                        const SizedBox(width: 8),
                        Text(
                          context.l10n.signInWithApple,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w600,
                            fontFamily: 'Manrope',
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
              ],

              // No Google sign-in: a self-hosted deployment has no Google OAuth client.

              // Local dev: skip Google/Apple OAuth, sign in with the
              // username/password provisioned on the server (see
              // backend/scripts/seed_local_account.py) — not anonymous and not
              // a shared harness user, so the same account is reachable from
              // any device and survives an app relaunch.
              if (provider.isLocalDevProfile) ...[
                Row(
                  children: [
                    Expanded(
                      child: _LoginModeButton(
                        label: 'Local network',
                        selected: !_remoteLogin,
                        onTap: () => setState(() => _remoteLogin = false),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: _LoginModeButton(
                        label: 'Remote (internet)',
                        selected: _remoteLogin,
                        onTap: () => setState(() => _remoteLogin = true),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _usernameController,
                  style: TextStyle(color: OmiColors.textPrimary, fontSize: 14, fontFamily: 'Manrope'),
                  decoration: _fieldDecoration(label: 'Username', hint: 'Username', icon: Icons.person),
                  autocorrect: false,
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _passwordController,
                  style: TextStyle(color: OmiColors.textPrimary, fontSize: 14, fontFamily: 'Manrope'),
                  decoration: _fieldDecoration(label: 'Password', hint: 'Password', icon: Icons.lock),
                  obscureText: true,
                  autocorrect: false,
                  onChanged: (_) => setState(() {}),
                  onSubmitted: (_) {
                    if (!_canSubmitLogin) return;
                    _submitLogin(provider);
                  },
                ),
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  height: 56,
                  child: ElevatedButton(
                    onPressed: !_canSubmitLogin
                        ? null
                        : () {
                            OmiHaptics.selection();
                            _submitLogin(provider);
                          },
                    style: ElevatedButton.styleFrom(
                      backgroundColor: OmiColors.accent,
                      foregroundColor: OmiColors.onAccent,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
                    ),
                    child: const Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(Icons.developer_mode, size: 22),
                        SizedBox(width: 8),
                        Text(
                          'Log In',
                          style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600, fontFamily: 'Manrope'),
                        ),
                      ],
                    ),
                  ),
                ),
              ],

              const SizedBox(height: 24),

              // Privacy policy text (same as welcome page)
              RichText(
                textAlign: TextAlign.center,
                text: TextSpan(
                  style: TextStyle(
                    color: OmiColors.textPrimary.withValues(alpha: 0.6),
                    fontSize: 11,
                    fontFamily: 'Manrope',
                  ),
                  children: [
                    TextSpan(text: context.l10n.byContinuingAgree),
                    TextSpan(
                      text: context.l10n.privacyPolicy,
                      style: const TextStyle(decoration: TextDecoration.underline),
                      recognizer: TapGestureRecognizer()..onTap = provider.openPrivacyPolicy,
                    ),
                    const TextSpan(text: ' & '),
                    TextSpan(
                      text: context.l10n.termsOfUse,
                      style: const TextStyle(decoration: TextDecoration.underline),
                      recognizer: TapGestureRecognizer()..onTap = provider.openTermsOfService,
                    ),
                    const TextSpan(text: '.'),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _LoginModeButton extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _LoginModeButton({required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 10),
        decoration: BoxDecoration(
          color: selected ? OmiColors.accent : OmiColors.surface2,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: selected ? OmiColors.accent : OmiColors.border),
        ),
        child: Text(
          label,
          textAlign: TextAlign.center,
          style: TextStyle(
            color: selected ? OmiColors.onAccent : OmiColors.textSecondary,
            fontSize: 13,
            fontWeight: FontWeight.w600,
            fontFamily: 'Manrope',
          ),
        ),
      ),
    );
  }
}
