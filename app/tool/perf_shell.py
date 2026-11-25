#!/usr/bin/env python3
"""Materialize main.dart's actual shell without starting Firebase/native services.

Only the page and navigator key alias differ (the self-hosted app has no analytics wrappers).
Unknown source shapes fail closed. --check is part of both local and CI gates.
"""
import argparse
import hashlib
from pathlib import Path
import re

APP = Path(__file__).resolve().parents[1]
OUTPUT = APP / 'integration_test/perf/production_shell.dart'


def generate(source):
    build = source[source.index('  Widget build(BuildContext context) {', source.index('class _MyAppState')):]
    if not re.match(r'  Widget build\(BuildContext context\) \{\s*return MultiProvider\(', build):
        raise ValueError('Production root wrapper changed: extract a shared shell hook before accepting this shape')
    start = source.index('      builder: (context, child) {', source.index('class _MyAppState'))
    end = source.index('\n    );\n  }\n}', start)
    shell = source[start:end].strip()
    if not shell.endswith('},') or shell.count('MaterialApp(') != 1:
        raise ValueError('Production shell shape changed: expose a shared shell hook; never omit wrappers')
    substitutions = {
        'const AppShell()': 'widget.page',
    }
    for old, new in substitutions.items():
        if shell.count(old) != 1:
            raise ValueError(f'Production shell boundary changed: expected exactly one {old}')
        shell = shell.replace(old, new)
    imports = '\n'.join(re.findall(r'^import .*?;', source, re.M))
    # Fingerprint only the mirrored content (imports, builder chain) so unrelated
    # main.dart edits do not rotate the digest; wrapper changes inside the
    # extracted shell still fail --check.
    digest = hashlib.sha256(f'{imports}\n\n{shell}\n'.encode()).hexdigest()
    return f'''// Derived by python3 tool/perf_shell.py from main.dart. Do not hand edit.
// Extracted shell SHA256 (imports, builder chain): {digest}
// ignore_for_file: unused_import, unnecessary_import
{imports}
import 'package:omi/main.dart' show MyApp, CustomErrorWidget;

/// The exact production builder chain, with an inert seeded page boundary.
class PerfProductionShell extends StatefulWidget {{
  const PerfProductionShell({{super.key, required this.page}});
  final Widget page;
  @override
  State<PerfProductionShell> createState() => _PerfProductionShellState();
}}
class _PerfProductionShellState extends State<PerfProductionShell> {{
  @override
  Widget build(BuildContext context) => MultiProvider(
    providers: [
      ChangeNotifierProvider(create: (_) => AppearanceProvider()),
      ChangeNotifierProvider(create: (_) => LocaleProvider()),
    ],
    {shell}
  );
}}
'''


def mirrors(source, committed):
    # Dart format may change indentation, but cannot change tokens.
    expected = re.sub(r'\s+', '', generate(source))
    return committed is not None and re.sub(r'\s+', '', committed) == expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    source = (APP / 'lib/main.dart').read_text()
    if args.check:
        if not mirrors(source, OUTPUT.read_text() if OUTPUT.exists() else None):
            raise SystemExit('PERF RED: production shell drifted. Run python3 app/tool/perf_shell.py, review all new wrappers, then rerun the gate.')
        print('Production shell fidelity: PASS (source checked)')
    else:
        OUTPUT.write_text(generate(source))


if __name__ == '__main__':
    main()
