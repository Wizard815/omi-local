import 'dart:io';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import 'package:provider/provider.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/providers/auth_provider.dart';
import 'package:omi/env/environment_profile.dart';
import 'package:omi/env/env.dart';
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

  @override
  Widget build(BuildContext context) {
    return Consumer<AuthenticationProvider>(
      builder: (context, provider, child) {
        return Column(
          children: [
            // Background image area - takes remaining space
            Expanded(
              child: Container(),
            ),

            // Bottom drawer card - wraps content
            Container(
              width: double.infinity,
              padding: EdgeInsets.fromLTRB(32, 26, 32, MediaQuery.of(context).padding.bottom + 8),
              decoration: const BoxDecoration(
                color: Colors.black,
                borderRadius: BorderRadius.only(topLeft: Radius.circular(40), topRight: Radius.circular(40)),
              ),
              child: SafeArea(
                top: false,
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      SizedBox(
                        height: 20,
                        child: provider.loading
                            ? const Center(
                                child: CircularProgressIndicator(valueColor: AlwaysStoppedAnimation(Colors.white)),
                              )
                            : null,
                      ),

                      // Title text
                      Text(
                        context.l10n.speakTranscribeSummarize,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 32,
                          fontWeight: FontWeight.bold,
                          height: 1.2,
                          fontFamily: 'Manrope',
                        ),
                        textAlign: TextAlign.center,
                      ),

                      const SizedBox(height: 24),

                      // ── Server URL field (local dev only) ──
                      if (Env.profile == AppEnvironmentProfile.localDev) ...[
                        TextField(
                          controller: _serverUrlController,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 14,
                            fontFamily: 'Manrope',
                          ),
                          decoration: InputDecoration(
                            hintText: 'http://192.168.20.5:8000/',
                            hintStyle: TextStyle(color: Colors.white.withValues(alpha: 0.35), fontSize: 14),
                            labelText: 'Server URL',
                            labelStyle: const TextStyle(color: Color(0xFF64D2FF), fontSize: 13),
                            prefixIcon: const Icon(Icons.dns, color: Color(0xFF64D2FF), size: 20),
                            filled: true,
                            fillColor: const Color(0x1AFFFFFF),
                            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0x33FFFFFF)),
                            ),
                            enabledBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0x33FFFFFF)),
                            ),
                            focusedBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0xFF64D2FF)),
                            ),
                          ),
                          keyboardType: TextInputType.url,
                          autocorrect: false,
                          onChanged: (_) => setState(() {}),
                        ),
                        const SizedBox(height: 12),
                      ],

                      // Sign in buttons
                      if (Platform.isIOS || Platform.isAndroid) ...[
                        SizedBox(
                          width: double.infinity,
                          height: 56,
                          child: ElevatedButton(
                            onPressed: () {
                              HapticFeedback.mediumImpact();
                              provider.onAppleSignIn(widget.onSignIn);
                            },
                            style: ElevatedButton.styleFrom(
                              backgroundColor: Colors.white,
                              foregroundColor: Colors.black,
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

                      // Local dev: skip Google/Apple OAuth, sign in with the
                      // username/password provisioned on the server (see
                      // backend/scripts/seed_local_account.py) — not anonymous,
                      // so the same account is reachable from any device and
                      // survives an app relaunch.
                      if (Env.profile == AppEnvironmentProfile.localDev) ...[
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
                          style: const TextStyle(color: Colors.white, fontSize: 14, fontFamily: 'Manrope'),
                          decoration: InputDecoration(
                            hintText: 'Username',
                            hintStyle: TextStyle(color: Colors.white.withValues(alpha: 0.35), fontSize: 14),
                            labelText: 'Username',
                            labelStyle: const TextStyle(color: Color(0xFF64D2FF), fontSize: 13),
                            prefixIcon: const Icon(Icons.person, color: Color(0xFF64D2FF), size: 20),
                            filled: true,
                            fillColor: const Color(0x1AFFFFFF),
                            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0x33FFFFFF)),
                            ),
                            enabledBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0x33FFFFFF)),
                            ),
                            focusedBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0xFF64D2FF)),
                            ),
                          ),
                          autocorrect: false,
                          onChanged: (_) => setState(() {}),
                        ),
                        const SizedBox(height: 12),
                        TextField(
                          controller: _passwordController,
                          style: const TextStyle(color: Colors.white, fontSize: 14, fontFamily: 'Manrope'),
                          decoration: InputDecoration(
                            hintText: 'Password',
                            hintStyle: TextStyle(color: Colors.white.withValues(alpha: 0.35), fontSize: 14),
                            labelText: 'Password',
                            labelStyle: const TextStyle(color: Color(0xFF64D2FF), fontSize: 13),
                            prefixIcon: const Icon(Icons.lock, color: Color(0xFF64D2FF), size: 20),
                            filled: true,
                            fillColor: const Color(0x1AFFFFFF),
                            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0x33FFFFFF)),
                            ),
                            enabledBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0x33FFFFFF)),
                            ),
                            focusedBorder: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(16),
                              borderSide: const BorderSide(color: Color(0xFF64D2FF)),
                            ),
                          ),
                          obscureText: true,
                          autocorrect: false,
                          onChanged: (_) => setState(() {}),
                          onSubmitted: (_) {
                            if (_usernameController.text.trim().isEmpty || _passwordController.text.isEmpty) return;
                            _submitLogin(provider);
                          },
                        ),
                        const SizedBox(height: 12),
                        SizedBox(
                          width: double.infinity,
                          height: 56,
                          child: ElevatedButton(
                            onPressed: (_usernameController.text.trim().isEmpty || _passwordController.text.isEmpty)
                                ? null
                                : () {
                                    HapticFeedback.mediumImpact();
                                    _submitLogin(provider);
                                  },
                            style: ElevatedButton.styleFrom(
                              backgroundColor: const Color(0xFF64D2FF),
                              foregroundColor: Colors.black,
                              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
                            ),
                            child: const Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Icon(Icons.developer_mode, size: 22),
                                SizedBox(width: 8),
                                Text(
                                  'Log In',
                                  style: TextStyle(
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

                      // Google sign in button
                      SizedBox(
                        width: double.infinity,
                        height: 56,
                        child: ElevatedButton(
                          onPressed: () {
                            HapticFeedback.mediumImpact();
                            provider.onGoogleSignIn(widget.onSignIn);
                          },
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.white,
                            foregroundColor: Colors.black,
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
                          ),
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              const FaIcon(FontAwesomeIcons.google, size: 20),
                              const SizedBox(width: 8),
                              Text(
                                context.l10n.signInWithGoogle,
                                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600, fontFamily: 'Manrope'),
                              ),
                            ],
                          ),
                        ),
                      ),

                      const SizedBox(height: 24),

                      // Privacy policy text
                      RichText(
                        textAlign: TextAlign.center,
                        text: TextSpan(
                          style: TextStyle(
                            color: Colors.white.withValues(alpha: 0.6),
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
                ),
              ),
            ),
          ],
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
          color: selected ? const Color(0xFF64D2FF) : const Color(0x1AFFFFFF),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: selected ? const Color(0xFF64D2FF) : const Color(0x33FFFFFF)),
        ),
        child: Text(
          label,
          textAlign: TextAlign.center,
          style: TextStyle(
            color: selected ? Colors.black : Colors.white70,
            fontSize: 13,
            fontWeight: FontWeight.w600,
            fontFamily: 'Manrope',
          ),
        ),
      ),
    );
  }
}