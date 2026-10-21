import 'package:flutter_test/flutter_test.dart';

import 'package:omi/pages/day_review/day_review_banner.dart';

void main() {
  test('nudges about the latest earlier unsorted day', () {
    expect(
      dayToNudge(today: '2026-10-11', pendingDates: ['2026-10-08', '2026-10-10'], now: DateTime(2026, 10, 11, 9)),
      '2026-10-10',
    );
  });

  test('today only once it is evening', () {
    expect(dayToNudge(today: '2026-10-11', pendingDates: ['2026-10-11'], now: DateTime(2026, 10, 11, 14)), isNull);
    expect(
        dayToNudge(today: '2026-10-11', pendingDates: ['2026-10-11'], now: DateTime(2026, 10, 11, 20)), '2026-10-11');
  });

  test('nothing pending, nothing shown', () {
    expect(dayToNudge(today: '2026-10-11', pendingDates: const [], now: DateTime(2026, 10, 11, 23)), isNull);
  });
}
