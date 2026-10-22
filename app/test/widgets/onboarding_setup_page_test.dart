import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/onboarding/setup_page.dart';

// The "Setting up your Omi" page: the checklist ticks on a timer and the page advances itself once
// the ticks are done and the pending setup settled.
//
// Timers drive the page, so the tests pump explicit durations: pumpAndSettle would run every tick
// at once and never settle on the active step's spinner.

const _interval = Duration(milliseconds: 750);
const _transition = Duration(milliseconds: 400);

Widget _host(Widget child, {TargetPlatform platform = TargetPlatform.iOS}) {
  return MaterialApp(
    theme: ThemeData(platform: platform),
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    supportedLocales: AppLocalizations.supportedLocales,
    home: Scaffold(body: child),
  );
}

int _doneCount() => find.byKey(const ValueKey('done')).evaluate().length;

void main() {
  testWidgets('steps tick one by one and the page advances once after the last tick', (tester) async {
    var finished = 0;
    await tester.pumpWidget(_host(OnboardingSetupPage(
      onFinished: () => finished++,
      stepInterval: _interval,
    )));
    await tester.pump();
    expect(find.byKey(const Key('onboarding_setup_page')), findsOneWidget);
    expect(find.text('Setting up your Omi'), findsOneWidget);
    expect(find.text('Give Omi a moment to personalize'), findsOneWidget);
    for (var i = 0; i < OnboardingSetupPage.stepCount; i++) {
      expect(find.byKey(Key('onboarding_setup_step_$i')), findsOneWidget);
    }
    expect(_doneCount(), 0);

    await tester.pump(_interval); // t = 750: first tick
    await tester.pump(_transition);
    expect(_doneCount(), 1);
    await tester.pump(_interval - _transition); // t = 1500: second tick
    await tester.pump(_transition);
    expect(_doneCount(), 2);

    await tester.pump(_interval * 3 - _transition); // t = 3750: last tick
    await tester.pump(_transition);
    expect(_doneCount(), OnboardingSetupPage.stepCount);
    expect(finished, 0, reason: 'the last tick is given a beat before leaving');

    await tester.pump(const Duration(seconds: 1));
    expect(finished, 1);
    await tester.pump(const Duration(seconds: 5));
    expect(finished, 1, reason: 'advances exactly once');
  });

  testWidgets('waits for pending setup, but never longer than the cap', (tester) async {
    var finished = 0;
    final pending = Completer<void>();
    await tester.pumpWidget(_host(OnboardingSetupPage(
      onFinished: () => finished++,
      pendingWork: pending.future,
      stepInterval: _interval,
      maxPendingWait: const Duration(seconds: 3),
    )));
    await tester.pump(_interval * OnboardingSetupPage.stepCount); // t = 3750: ticks done
    await tester.pump(const Duration(seconds: 2));
    expect(finished, 0, reason: 'still waiting on real setup');
    await tester.pump(const Duration(seconds: 2)); // cap (3 s) + beat passed
    expect(finished, 1, reason: 'the cap ran out; never block on setup for longer');
  });

  testWidgets('pending setup finishing early releases the page right after the last tick', (tester) async {
    var finished = 0;
    await tester.pumpWidget(_host(OnboardingSetupPage(
      onFinished: () => finished++,
      pendingWork: Future<void>.value(),
      stepInterval: _interval,
    )));
    await tester.pump(_interval * OnboardingSetupPage.stepCount);
    await tester.pump(const Duration(seconds: 1));
    expect(finished, 1);
  });
}
