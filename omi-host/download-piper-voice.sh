#!/usr/bin/env bash
# Download a Piper TTS voice into the shared audio volume (/data/piper/voices).
# Runs on the Unraid host. Default voice: en_US-lessac-medium (~60 MB).
#
#   bash omi-host/download-piper-voice.sh [VOICE]
#   VOICE format: en_US-lessac-medium  (lang/voice/quality)
set -euo pipefail

VOICE="${1:-en_US-lessac-medium}"
DEST="${PIPER_VOICE_DIR:-/mnt/user/appdata/omi-local-audio/piper/voices}"
# parse lang_locale-name-quality
LC="${VOICE%%-*}"                    # en_US
REST="${VOICE#*-}"                    # lessac-medium
NAME="${REST%-*}"                     # lessac
QUALITY="${REST##*-}"                 # medium
LANGDIR="${LC%_*}"                    # en

BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/${LANGDIR}/${LC}/${NAME}/${QUALITY}"
mkdir -p "$DEST"
cd "$DEST"

UA="Mozilla/5.0 (X11; Linux x86_64) OMI-local/1.0"
for ext in onnx onnx.json; do
  f="${VOICE}.${ext}"
  if [ -s "$f" ]; then
    echo "have $f ($(du -h "$f" | cut -f1))"
    continue
  fi
  for attempt in 1 2 3 4; do
    if curl -fSL --retry 0 -A "$UA" -o "$f.part" "${BASE}/${VOICE}.${ext}"; then
      mv "$f.part" "$f"
      echo "downloaded $f ($(du -h "$f" | cut -f1))"
      break
    fi
    rm -f "$f.part"
    echo "  attempt $attempt failed, retrying in $((attempt*6))s ..."
    sleep $((attempt*6))
  done
  [ -s "$f" ] || { echo "error: could not download $f (HF rate limit?)" >&2; exit 1; }
done

echo
echo "Voice ready: $DEST/$VOICE.onnx"
echo "The omi-audio container sees it at \$OMI_PIPER_VOICE (default /data/piper/voices/${VOICE}.onnx)."
[ "$VOICE" = "en_US-lessac-medium" ] || echo "NOTE: set OMI_PIPER_VOICE=/data/piper/voices/${VOICE}.onnx on the audio service."
