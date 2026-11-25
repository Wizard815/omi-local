import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'package:omi/services/phone_llm/phone_llm_client.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// "AI on this phone" (Android only): where chat is answered, the phone model's address, key and
/// model, a Test, and whether recent conversations go along as context. Shown on the chat's /model
/// sheet and in Settings. Every change applies at once (lib/services/phone_llm/README.md).
class PhoneLlmSection extends StatefulWidget {
  const PhoneLlmSection({super.key, this.isAndroid, this.settings, this.clientFor});

  /// Injected so widget tests cover both platforms; defaults to the running platform.
  final bool? isAndroid;
  final PhoneLlmSettings? settings;
  final PhoneLlmClient Function(PhoneLlmSettings settings)? clientFor;

  @override
  State<PhoneLlmSection> createState() => _PhoneLlmSectionState();
}

class _PhoneLlmSectionState extends State<PhoneLlmSection> {
  late final PhoneLlmSettings _settings = widget.settings ?? PhoneLlmSettings.instance;
  final _address = TextEditingController();
  final _key = TextEditingController();
  Timer? _save;
  bool _loaded = false;
  List<String> _models = const [];
  bool _loadingModels = false;
  String? _modelsError;
  bool _testing = false;
  String? _testResult;

  bool get _android => widget.isAndroid ?? Platform.isAndroid;

  PhoneLlmClient _client() =>
      (widget.clientFor ?? (s) => PhoneLlmClient(baseUrl: s.baseUrl, apiKey: s.apiKey))(_settings);

  @override
  void initState() {
    super.initState();
    if (!_android) return;
    unawaited(_settings.ensureLoaded().then((_) {
      if (!mounted) return;
      _address.text = _settings.baseUrl;
      _key.text = _settings.apiKey;
      setState(() => _loaded = true);
      unawaited(_loadModels());
    }));
  }

  @override
  void dispose() {
    _flush();
    _address.dispose();
    _key.dispose();
    super.dispose();
  }

  void _flush() {
    if (_save?.isActive ?? false) {
      _save!.cancel();
      unawaited(_settings.setBaseUrl(_address.text));
      unawaited(_settings.setApiKey(_key.text));
    }
  }

  void _edited() {
    _save?.cancel();
    _save = Timer(const Duration(milliseconds: 600), () {
      unawaited(_settings.setBaseUrl(_address.text));
      unawaited(_settings.setApiKey(_key.text));
    });
  }

  String _problem(Object e) => e is PhoneLlmException && e.code == PhoneLlmError.unauthorized
      ? context.l10n.phoneLlmUnauthorized
      : context.l10n.phoneLlmNoModels;

  Future<void> _loadModels() async {
    _flush();
    setState(() {
      _loadingModels = true;
      _modelsError = null;
    });
    try {
      final models = await _client().listModels();
      if (!mounted) return;
      setState(() {
        _models = models;
        _modelsError = models.isEmpty ? context.l10n.phoneLlmNoModels : null;
      });
      // One model on the server (llama-server's usual case) is picked for the user.
      if (_settings.model.isEmpty && models.length == 1) await _settings.setModel(models.single);
    } catch (e) {
      if (mounted) setState(() => _modelsError = _problem(e));
    } finally {
      if (mounted) setState(() => _loadingModels = false);
    }
  }

  Future<void> _test() async {
    _flush();
    setState(() {
      _testing = true;
      _testResult = null;
    });
    String result;
    try {
      final took = await _client().test(_settings.model);
      result = mounted ? context.l10n.phoneLlmTestResult('${took.inMilliseconds}') : '';
    } catch (e) {
      result = !mounted
          ? ''
          : e is PhoneLlmException && e.code == PhoneLlmError.unauthorized
              ? context.l10n.phoneLlmUnauthorized
              : context.l10n.phoneLlmTestFailed;
    }
    if (mounted) {
      setState(() {
        _testing = false;
        _testResult = result;
      });
    }
  }

  Widget _check(bool selected) =>
      selected ? Icon(Icons.check_rounded, color: OmiColors.accent, size: 20) : const SizedBox(width: 20);

  Widget _field(TextEditingController controller, String label, {bool obscure = false, Key? key}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: OmiSpacing.sm),
      child: TextField(
        key: key,
        controller: controller,
        obscureText: obscure,
        autocorrect: false,
        enableSuggestions: false,
        keyboardType: obscure ? TextInputType.visiblePassword : TextInputType.url,
        onChanged: (_) => _edited(),
        onSubmitted: (_) => _flush(),
        style: OmiType.body.copyWith(color: OmiColors.textPrimary),
        decoration: InputDecoration(
          labelText: label,
          labelStyle: TextStyle(color: OmiColors.textSecondary),
          filled: true,
          fillColor: OmiColors.surface1,
          border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (!_android) return const SizedBox.shrink();
    final l10n = context.l10n;
    if (!_loaded) return const Center(child: OmiSpinner());
    return ListenableBuilder(
      listenable: _settings,
      builder: (context, _) {
        final modes = {
          PhoneLlmMode.server: l10n.phoneLlmModeServer,
          PhoneLlmMode.phone: l10n.phoneLlmModePhone,
          PhoneLlmMode.auto: l10n.phoneLlmModeAuto,
        };
        final models = {..._models, if (_settings.model.isNotEmpty) _settings.model}.toList();
        return Column(
          key: const ValueKey('phone_llm_section'),
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            OmiSectionHeader(l10n.phoneLlmTitle, subtitle: l10n.phoneLlmHint),
            OmiSettingsGroup(
              header: l10n.phoneLlmSource,
              children: [
                for (final entry in modes.entries)
                  OmiSettingsRow(
                    key: ValueKey('phone_llm_mode_${entry.key.wire}'),
                    title: entry.value,
                    trailing: _check(_settings.mode == entry.key),
                    showChevron: false,
                    onTap: () => _settings.setMode(entry.key),
                  ),
              ],
            ),
            const SizedBox(height: OmiSpacing.lg),
            _field(_address, l10n.phoneLlmAddress, key: const ValueKey('phone_llm_address')),
            _field(_key, l10n.apiKey, obscure: true, key: const ValueKey('phone_llm_api_key')),
            OmiSectionHeader(
              l10n.model,
              trailing: _loadingModels
                  ? const OmiSpinner()
                  : OmiIconButton(
                      icon: const Icon(Icons.refresh_rounded),
                      label: l10n.refresh,
                      onPressed: _loadModels,
                    ),
            ),
            OmiSettingsGroup(
              children: [
                for (final id in models)
                  OmiSettingsRow(
                    key: ValueKey('phone_llm_model_$id'),
                    title: id,
                    trailing: _check(_settings.model == id),
                    showChevron: false,
                    onTap: () => _settings.setModel(id),
                  ),
                if (models.isEmpty || _modelsError != null)
                  OmiSettingsRow(title: _modelsError ?? l10n.phoneLlmNoModels, showChevron: false),
              ],
            ),
            const SizedBox(height: OmiSpacing.md),
            Row(
              children: [
                OmiButton.secondary(
                  key: const ValueKey('phone_llm_test'),
                  label: l10n.connectionsTest,
                  size: OmiButtonSize.compact,
                  isLoading: _testing,
                  onPressed: _testing || !_settings.configured ? null : _test,
                ),
                const SizedBox(width: OmiSpacing.sm),
                if (_testResult != null)
                  Expanded(
                    child: Text(
                      _testResult!,
                      key: const ValueKey('phone_llm_test_result'),
                      style: OmiType.footnote.copyWith(color: OmiColors.textSecondary),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: OmiSpacing.md),
            OmiSettingsGroup(
              children: [
                OmiSettingsRow.toggle(
                  key: const ValueKey('phone_llm_include_conversations'),
                  title: l10n.phoneLlmIncludeConversations,
                  value: _settings.includeConversations,
                  onChanged: _settings.setIncludeConversations,
                ),
              ],
            ),
          ],
        );
      },
    );
  }
}
