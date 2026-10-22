#!/usr/bin/env bash
# Build the self-hosted "Omi Local" Android APK (dev flavor) as a release,
# arm64-only, obfuscated build. Roughly half the size of the documented
# `flutter build apk --debug --flavor dev` (debug JIT, all ABIs), which is the
# wrong shape for a phone install: it ships the JIT snapshot plus x86_64/armv7
# native libs nobody runs. The dev flavor is release-signed with the debug
# keystore (android/app/build.gradle: productFlavors.dev.signingConfig
# signingConfigs.debug), so no release keystore is needed.
#
# Usage: app/scripts/build_local_apk.sh [extra flutter build args]
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

flutter build apk \
  --release \
  --flavor dev \
  --target-platform android-arm64 \
  --split-debug-info=build/symbols \
  --obfuscate \
  "$@"

APK="$APP_DIR/build/app/outputs/flutter-apk/app-dev-release.apk"
if [[ ! -f "$APK" ]]; then
  echo "error: expected APK not found at $APK" >&2
  exit 1
fi
SIZE_BYTES=$(stat -c %s "$APK" 2>/dev/null || stat -f %z "$APK")
SIZE_MB=$(awk -v b="$SIZE_BYTES" 'BEGIN { printf "%.1f", b / 1048576 }')
echo "APK: $APK"
echo "Size: ${SIZE_MB} MB (${SIZE_BYTES} bytes)"
echo "Symbols (keep for stack traces): $APP_DIR/build/symbols"
