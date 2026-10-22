import 'dart:async';

import 'package:flutter/material.dart';

import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// "Setting up your Omi": the last moment before the completion screen. A short checklist ticks
/// through the personalization steps while any setup the flow started earlier ([pendingWork])
/// finishes, then the page advances itself through [onFinished].
///
/// Shown only behind [OnboardingSetupRatingPromptGate]; the wrapper owns that decision.
class OnboardingSetupPage extends StatefulWidget {
  const OnboardingSetupPage({
    super.key,
    required this.onFinished,
    this.pendingWork,
    this.stepInterval = const Duration(milliseconds: 750),
    this.maxPendingWait = const Duration(seconds: 5),
  });

  /// Called once, after the last tick and once [pendingWork] settled (or [maxPendingWait] ran out).
  final VoidCallback onFinished;

  /// Setup the flow already started (the knowledge-graph prebuild); null means nothing to wait for.
  final Future<void>? pendingWork;

  /// Gap between two ticks.
  final Duration stepInterval;

  /// Longest the page waits for [pendingWork] after the ticks finished.
  final Duration maxPendingWait;

  static const int stepCount = 5;

  @override
  State<OnboardingSetupPage> createState() => _OnboardingSetupPageState();
}

class _OnboardingSetupPageState extends State<OnboardingSetupPage> with SingleTickerProviderStateMixin {
  late final AnimationController _entrance;
  final List<Timer> _timers = [];
  int _completed = 0;
  bool _finished = false;

  @override
  void initState() {
    super.initState();
    _entrance = AnimationController(vsync: this, duration: const Duration(milliseconds: 500))..forward();
    for (var i = 1; i <= OnboardingSetupPage.stepCount; i++) {
      _timers.add(Timer(widget.stepInterval * i, () => _tick(i)));
    }
  }

  @override
  void dispose() {
    for (final timer in _timers) {
      timer.cancel();
    }
    _entrance.dispose();
    super.dispose();
  }

  void _tick(int completed) {
    if (!mounted) return;
    setState(() => _completed = completed);
    OmiHaptics.selection();
    if (completed == OnboardingSetupPage.stepCount) unawaited(_finishWhenReady());
  }

  Future<void> _finishWhenReady() async {
    final work = widget.pendingWork;
    if (work != null) {
      // Real setup may still be running; wait for it, but never longer than the cap.
      await work.timeout(widget.maxPendingWait, onTimeout: () {}).catchError((_) {});
    }
    // Let the last tick land before leaving.
    await Future<void>.delayed(const Duration(milliseconds: 450));
    if (!mounted || _finished) return;
    _finished = true;
    widget.onFinished();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final steps = [
      l10n.onboardingSetupStepWorkspace,
      l10n.onboardingSetupStepLanguage,
      l10n.onboardingSetupStepMemory,
      l10n.onboardingSetupStepDevices,
      l10n.onboardingSetupStepPersonalize,
    ];
    assert(steps.length == OnboardingSetupPage.stepCount);
    return Container(
      key: const Key('onboarding_setup_page'),
      color: OmiColors.surface0,
      width: double.infinity,
      height: double.infinity,
      child: SafeArea(
        child: FadeTransition(
          opacity: CurvedAnimation(parent: _entrance, curve: Curves.easeOut),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.xxl),
            child: LayoutBuilder(
              builder: (context, constraints) => SingleChildScrollView(
                child: ConstrainedBox(
                  constraints: BoxConstraints(minHeight: constraints.maxHeight),
                  child: IntrinsicHeight(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Spacer(flex: 2),
                        Semantics(
                          header: true,
                          child: Text(l10n.onboardingSetupTitle, style: OmiType.title1.copyWith(height: 1.2)),
                        ),
                        const SizedBox(height: OmiSpacing.xs),
                        Text(
                          l10n.onboardingSetupSubtitle,
                          style: OmiType.body.copyWith(color: OmiColors.textSecondary, height: 1.4),
                        ),
                        const SizedBox(height: OmiSpacing.xxl + OmiSpacing.md),
                        for (var i = 0; i < steps.length; i++)
                          _SetupStepRow(
                            key: Key('onboarding_setup_step_$i'),
                            label: steps[i],
                            state: i < _completed
                                ? _StepState.done
                                : i == _completed
                                    ? _StepState.active
                                    : _StepState.pending,
                          ),
                        const Spacer(flex: 3),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

enum _StepState { pending, active, done }

class _SetupStepRow extends StatelessWidget {
  const _SetupStepRow({super.key, required this.label, required this.state});

  final String label;
  final _StepState state;

  static const double _indicator = 26;

  @override
  Widget build(BuildContext context) {
    final motion = OmiMotion.of(context);
    final done = state == _StepState.done;
    final textColor = state == _StepState.pending ? OmiColors.textTertiary : OmiColors.textPrimary;
    return Semantics(
      checked: done,
      label: label,
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: OmiSpacing.sm),
        child: Row(
          children: [
            SizedBox.square(
              dimension: _indicator,
              child: AnimatedSwitcher(
                duration: motion.standard,
                switchInCurve: Curves.easeOutBack,
                switchOutCurve: Curves.easeIn,
                transitionBuilder: (child, animation) => ScaleTransition(
                  scale: animation,
                  child: FadeTransition(opacity: animation, child: child),
                ),
                child: switch (state) {
                  _StepState.done => Container(
                      key: const ValueKey('done'),
                      decoration: BoxDecoration(color: OmiColors.textPrimary, shape: BoxShape.circle),
                      child: Icon(Icons.check_rounded, size: 16, color: OmiColors.onAccent),
                    ),
                  _StepState.active => Padding(
                      key: const ValueKey('active'),
                      padding: const EdgeInsets.all(1),
                      child: OmiSpinner(size: OmiSpinnerSize.regular, color: OmiColors.textPrimary),
                    ),
                  _StepState.pending => Container(
                      key: const ValueKey('pending'),
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        border: Border.all(color: OmiColors.border, width: 1.5),
                      ),
                    ),
                },
              ),
            ),
            const SizedBox(width: OmiSpacing.md),
            Expanded(
              child: AnimatedDefaultTextStyle(
                duration: motion.standard,
                curve: OmiMotion.standardCurve,
                style: OmiType.body.copyWith(color: textColor, height: 1.3),
                child: Text(label),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
