#!/usr/bin/env bash
# The omi-host tests that need only python3 and bash, for the local and CI lanes
# (.github/checks-manifest.yaml: omi-host-stdlib-tests). The audio-service tests
# need fastapi/numpy and still run by hand inside the omi-audio image.
set -euo pipefail
cd "$(dirname "$0")"
status=0
for test in \
  test_audio_env.py \
  test_auth_env.py \
  test_backup.py \
  test_caldav_env.py \
  test_caldav_demo_legacy_ref.py \
  test_egress_guard.py \
  test_health_watchdog.py \
  test_maintenance.py \
  test_merge_proxy_stream.py; do
  echo "== omi-host/$test"
  if ! python3 "$test"; then
    echo "FAILED: omi-host/$test" >&2
    status=1
  fi
done
exit "$status"
