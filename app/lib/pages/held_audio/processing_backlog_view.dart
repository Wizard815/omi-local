import 'package:flutter/material.dart';

import 'package:omi/providers/usage_provider.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

class ProcessingDeferredMarker extends StatelessWidget {
  const ProcessingDeferredMarker({super.key});

  @override
  Widget build(BuildContext context) => Text(
        // Self-hosted: finished clips wait for the day review, not for a budget.
        UsageProvider.isSelfHosted ? context.l10n.heldForDayReview : context.l10n.processingDeferred,
        key: const Key('processing_deferred_marker'),
        style: OmiType.footnote.copyWith(color: OmiColors.textSecondary),
      );
}
