// Renders every registered visual audit scenario without writing images, so a scenario that no
// longer compiles or throws fails the ordinary app suite. Screenshots: app/scripts/visual_audit.sh.
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/providers/usage_provider.dart';

import '../../integration_test/visual_audit/harness.dart';
import '../../integration_test/visual_audit/registry.dart';
import '../../integration_test/visual_audit/suite.dart' as active;

void main() {
  // These surfaces only exist with a billing plane; self-hosted builds turn it
  // off (UsageProvider.selfHosted). Run them with upstream's billing rules.
  setUp(() => UsageProvider.selfHosted = false);
  tearDown(() => UsageProvider.selfHosted = true);

  test('scenario ids are unique, lowercase and hyphenated, and every field is filled', () {
    final ids = auditSuite.scenarios.map((s) => s.id).toList();
    expect(ids.toSet().length, ids.length, reason: 'duplicate scenario id');
    for (final s in auditSuite.scenarios) {
      expect(s.id, matches(RegExp(r'^[a-z0-9]+(-[a-z0-9]+)*$')));
      expect([s.title, s.page, s.state].every((f) => f.trim().isNotEmpty), isTrue, reason: s.id);
      expect(s.page, startsWith('lib/'), reason: '${s.id}: page names the production file it pumps');
    }
  });

  test('capture_test.dart runs the current suite on main', () {
    expect(identical(active.auditSuite, auditSuite), isTrue, reason: 'suite.dart must export registry.dart');
  });

  runAuditScenarios(auditSuite);
}
