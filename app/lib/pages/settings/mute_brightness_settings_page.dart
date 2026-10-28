import 'package:flutter/material.dart';

import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import 'package:provider/provider.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/providers/capture_provider.dart';
import 'package:omi/services/mute_brightness_link.dart';
import 'package:omi/services/screen_brightness_service.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// omi-local, Android: the screen brightness while capture is muted and while it listens.
/// Turning it on asks Android for "Modify system settings" once; the levels apply at each mute
/// change (lib/services/mute_brightness_link.dart) and right away from here.
class MuteBrightnessSettingsPage extends StatefulWidget {
  const MuteBrightnessSettingsPage({super.key});

  @override
  State<MuteBrightnessSettingsPage> createState() => _MuteBrightnessSettingsPageState();
}

class _MuteBrightnessSettingsPageState extends State<MuteBrightnessSettingsPage> with WidgetsBindingObserver {
  final _prefs = SharedPreferencesUtil();
  late bool _enabled = _prefs.muteBrightnessEnabled;
  late double _muted = _prefs.mutedBrightnessPercent.toDouble();
  late double _normal = _prefs.unmutedBrightnessPercent.toDouble();
  bool _canWrite = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refreshPermission();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Back from the OS permission page.
    if (state == AppLifecycleState.resumed) _refreshPermission();
  }

  Future<void> _refreshPermission() async {
    final ok = await ScreenBrightnessService.instance.canWrite();
    if (mounted) setState(() => _canWrite = ok);
  }

  MuteBrightnessLink _link() {
    final capture = context.read<CaptureProvider>();
    return MuteBrightnessLink(muteSource: capture, isMuted: () => capture.isPaused);
  }

  Future<void> _setEnabled(bool value) async {
    setState(() => _enabled = value);
    _prefs.muteBrightnessEnabled = value;
    if (!value) return;
    if (!_canWrite) {
      await ScreenBrightnessService.instance.requestPermission();
      return;
    }
    await _link().applyNow();
  }

  Future<void> _saveLevels() async {
    _prefs.mutedBrightnessPercent = _muted.round();
    _prefs.unmutedBrightnessPercent = _normal.round();
    if (_enabled && _canWrite) await _link().applyNow();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.primary,
      appBar: AppBar(
        backgroundColor: Theme.of(context).colorScheme.primary,
        leading: const OmiBackButton(),
        title: Text(l10n.muteBrightnessTitle),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.sm, OmiSpacing.md, OmiSpacing.xl),
        children: [
          if (!ScreenBrightnessService.supported)
            Padding(
              padding: const EdgeInsets.only(bottom: OmiSpacing.md),
              child:
                  Text(l10n.muteBrightnessUnsupported, style: OmiType.subhead.copyWith(color: OmiColors.textSecondary)),
            ),
          OmiSettingsGroup(
            footer: l10n.muteBrightnessExplainer,
            children: [
              OmiSettingsRow.toggle(
                key: const Key('mute_brightness_toggle'),
                leading: const FaIcon(FontAwesomeIcons.sun),
                title: l10n.muteBrightnessToggle,
                value: _enabled,
                onChanged: ScreenBrightnessService.supported ? _setEnabled : null,
              ),
              if (_enabled && !_canWrite)
                OmiSettingsRow(
                  key: const Key('mute_brightness_permission'),
                  leading: const FaIcon(FontAwesomeIcons.shieldHalved),
                  title: l10n.muteBrightnessPermissionTitle,
                  subtitle: l10n.muteBrightnessPermissionBody,
                  value: l10n.muteBrightnessPermissionButton,
                  showChevron: true,
                  onTap: () => ScreenBrightnessService.instance.requestPermission(),
                ),
            ],
          ),
          const SizedBox(height: OmiSpacing.lg),
          _levelCard(
            key: const Key('mute_brightness_muted_slider'),
            title: l10n.muteBrightnessMutedLevel,
            value: _muted,
            onChanged: (v) => setState(() => _muted = v),
          ),
          const SizedBox(height: OmiSpacing.md),
          _levelCard(
            key: const Key('mute_brightness_normal_slider'),
            title: l10n.muteBrightnessNormalLevel,
            value: _normal,
            onChanged: (v) => setState(() => _normal = v),
          ),
        ],
      ),
    );
  }

  Widget _levelCard({
    required Key key,
    required String title,
    required double value,
    required ValueChanged<double> onChanged,
  }) {
    final percent = '${value.round()}%';
    return Container(
      padding: const EdgeInsets.all(OmiSpacing.lg),
      decoration: BoxDecoration(color: OmiColors.surface1, borderRadius: OmiRadius.lgAll),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(title, style: OmiType.headline),
              Text(percent, style: OmiType.subhead.copyWith(color: OmiColors.textSecondary)),
            ],
          ),
          const SizedBox(height: OmiSpacing.sm),
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
              activeTrackColor: OmiColors.accent,
              inactiveTrackColor: OmiColors.surface3,
              thumbColor: OmiColors.accent,
              overlayColor: OmiColors.accent.withValues(alpha: 0.12),
              trackHeight: 6,
              thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 10),
            ),
            child: Slider(
              key: key,
              value: value,
              min: 1,
              max: 100,
              divisions: 99,
              label: percent,
              semanticFormatterCallback: (v) => '${v.round()}%',
              onChanged: onChanged,
              onChangeEnd: (_) => _saveLevels(),
            ),
          ),
        ],
      ),
    );
  }
}
