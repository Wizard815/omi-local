import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/ui/ui.dart';

Widget _pair({double width = 358, double textScale = 1}) => MaterialApp(
      theme: buildOmiTheme(),
      home: MediaQuery(
        data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
        child: Scaffold(
          body: Align(
            alignment: Alignment.topLeft,
            child: SizedBox(
              width: width,
              child: OmiButtonPair(
                first: OmiButton(label: 'Sync Now', onPressed: () {}),
                second: OmiButton.secondary(label: 'Test Connection', onPressed: () {}),
              ),
            ),
          ),
        ),
      ),
    );

void main() {
  testWidgets('side by side on a regular phone', (tester) async {
    await tester.pumpWidget(_pair());
    final first = tester.getRect(find.text('Sync Now'));
    final second = tester.getRect(find.text('Test Connection'));
    expect(first.top, second.top);
    expect(first.right, lessThan(second.left));
  });

  testWidgets('stacked, first on top, on a 320 pt phone and at large text', (tester) async {
    for (final (width, scale) in [(288.0, 1.0), (358.0, 2.0)]) {
      await tester.pumpWidget(_pair(width: width, textScale: scale));
      expect(tester.takeException(), isNull);
      final first = tester.getRect(find.text('Sync Now'));
      final second = tester.getRect(find.text('Test Connection'));
      expect(second.top, greaterThan(first.bottom), reason: 'width $width at ${scale}x');
    }
  });

  testWidgets('a Cancel on the left goes under its primary when stacked', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildOmiTheme(),
      home: Scaffold(
        body: SizedBox(
          width: 288,
          child: OmiButtonPair(
            secondIsPrimary: true,
            secondFlex: 2,
            firstMinWidth: 90,
            secondMinWidth: 200,
            first: OmiButton.secondary(label: 'Cancel', onPressed: () {}),
            second: OmiButton(label: 'Approve Selected (6)', onPressed: () {}),
          ),
        ),
      ),
    ));
    expect(
        tester.getRect(find.text('Cancel')).top, greaterThan(tester.getRect(find.text('Approve Selected (6)')).bottom));
  });
}
