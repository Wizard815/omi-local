import 'package:flutter/widgets.dart';

import 'package:omi/ui/omi_tokens.dart';

/// Two actions side by side ("Allow Once" / "Deny", "Sync Now" / "Test Connection"); stacked full
/// width when a side would be too narrow for its label: a narrow screen or large accessibility
/// text. Stacked, the primary action is on top: [first] unless [secondIsPrimary] (a Cancel on the
/// left of the primary, as in an editor's Cancel / Save).
class OmiButtonPair extends StatelessWidget {
  const OmiButtonPair({
    super.key,
    required this.first,
    required this.second,
    this.secondIsPrimary = false,
    this.secondFlex = 1,
    this.firstMinWidth = defaultMinWidth,
    this.secondMinWidth = defaultMinWidth,
  });

  final Widget first;
  final Widget second;
  final bool secondIsPrimary;

  /// The second action's share of the row against the first's 1 (2 for a long primary label).
  final int secondFlex;

  /// The narrowest each side may be at 1x text before the pair stacks; scaled with the text.
  final double firstMinWidth;
  final double secondMinWidth;

  /// Holds a two-word Title Case label at 1x.
  static const double defaultMinWidth = 150;

  @override
  Widget build(BuildContext context) {
    final scale = MediaQuery.textScalerOf(context).scale(1);
    return LayoutBuilder(builder: (context, constraints) {
      final unit = (constraints.maxWidth - OmiSpacing.sm) / (1 + secondFlex);
      if (scale >= 1.5 || unit < firstMinWidth * scale || unit * secondFlex < secondMinWidth * scale) {
        final top = secondIsPrimary ? second : first;
        final bottom = secondIsPrimary ? first : second;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [top, const SizedBox(height: OmiSpacing.sm), bottom],
        );
      }
      return Row(children: [
        Expanded(child: first),
        const SizedBox(width: OmiSpacing.sm),
        Expanded(flex: secondFlex, child: second),
      ]);
    });
  }
}
