import 'package:flutter/widgets.dart';

/// Mobile session replay is a PostHog SDK feature. The self-hosted build ships
/// without PostHog (see [AnalyticsManager.init]), so nothing is ever recorded:
/// the scope keeps upstream's place in the app shell and passes its child
/// through unchanged.
class SessionReplayScope extends StatelessWidget {
  const SessionReplayScope({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) => child;
}
