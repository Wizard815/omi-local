#!/usr/bin/env python3
"""caldav_demo.py's legacy section: which revision is "the sync code from before per-person lists".

The default used to be `origin/main`, which stopped being the old code the day the
per-person change merged, and the demo then failed its own setup row. The default is now
the parent of the first commit that put `owner_tag` into backend/utils/caldav/sync.py.

What this pins down, on a scratch git repository:

  * the default resolves to a revision whose sync.py has no `owner_tag`, also after later
    commits on top of the change (the state of main after the merge),
  * a tree that never had the change uses its own HEAD,
  * an explicit --legacy-ref wins.

Run:  python3 test_caldav_demo_legacy_ref.py     (no pytest required)
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'demo'))

from caldav_demo import LEGACY_SYNC_PATH, legacy_sync_ref  # noqa: E402


def _git(repo, *args):
    return subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-C', repo, *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(repo, body, message):
    path = os.path.join(repo, LEGACY_SYNC_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as handle:
        handle.write(body)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', message)
    return _git(repo, 'rev-parse', 'HEAD')


def _sync_at(repo, ref):
    return _git(repo, 'show', f'{ref}:{LEGACY_SYNC_PATH}')


def _repo():
    repo = tempfile.mkdtemp(prefix='legacy-ref-')
    _git(repo, 'init', '-q')
    return repo


def test_default_is_the_code_before_the_change_even_after_it_merged():
    repo = _repo()
    old = _commit(repo, 'def sync(): pass\n', 'old sync')
    _commit(repo, 'def sync(owner_tag): pass\n', 'per-person UIDs')
    _commit(repo, 'def sync(owner_tag): return 1\n', 'later fix on main')
    ref = legacy_sync_ref(repo)
    assert 'owner_tag' not in _sync_at(repo, ref)
    assert _git(repo, 'rev-parse', ref) == old


def test_a_tree_without_the_change_uses_its_head():
    repo = _repo()
    _commit(repo, 'def sync(): pass\n', 'old sync')
    assert legacy_sync_ref(repo) == 'HEAD'


def test_an_explicit_ref_wins():
    repo = _repo()
    _commit(repo, 'def sync(): pass\n', 'old sync')
    assert legacy_sync_ref(repo, 'some-tag') == 'some-tag'


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items()) if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS: {test.__name__}')
    print(f'\nAll {len(tests)} tests passed.')
