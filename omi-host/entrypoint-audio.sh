#!/usr/bin/env bash
# Starts whisper-server (whisper.cpp, HIP/ROCm gfx906) in the background so the
# GPU model loads exactly once, waits for it to report ready, then hands off to
# local_audio_service.py. That process talks to whisper-server over HTTP for
# ASR (see _transcribe_whispercpp) -- VAD, diarization, WS streaming, and TTS
# all still run in-process there, unchanged.
#
# Set OMI_ASR_ENGINE=fasterwhisper to skip whisper-server entirely and use the
# original CPU faster-whisper path instead (local_audio_service.py loads that
# model itself in that case).
set -euo pipefail

if [ "${OMI_ASR_ENGINE:-whispercpp}" = "whispercpp" ]; then
    WHISPER_MODEL="${WHISPER_MODEL:-large-v3-turbo}"
    MODEL_DIR="${WHISPER_CPP_MODELS_DIR:-/data/whispercpp/models}"
    MODEL_FILE="${MODEL_DIR}/ggml-${WHISPER_MODEL}.bin"
    SERVER_BIN=/app/whisper.cpp/build/bin/whisper-server
    SERVER_URL="${WHISPER_CPP_SERVER_URL:-http://127.0.0.1:8081}"

    # Parse host:port out of the URL (used by whisper-server's own --host/--port).
    _hostport="${SERVER_URL#*://}"
    _hostport="${_hostport%%/*}"
    SERVER_HOST="${_hostport%%:*}"
    SERVER_PORT="${_hostport##*:}"

    mkdir -p "$MODEL_DIR"
    if [ ! -f "$MODEL_FILE" ]; then
        echo "Downloading ggml model '${WHISPER_MODEL}' into ${MODEL_DIR} ..."
        /app/whisper.cpp/models/download-ggml-model.sh "$WHISPER_MODEL" "$MODEL_DIR"
    else
        echo "Model cache hit: ${MODEL_FILE}"
    fi

    echo "Starting whisper-server (gfx906) on ${SERVER_HOST}:${SERVER_PORT} ..."
    "$SERVER_BIN" -m "$MODEL_FILE" --host "$SERVER_HOST" --port "$SERVER_PORT" \
        -t "${THREADS:-8}" &
    WHISPER_SERVER_PID=$!

    echo "Waiting for whisper-server to report ready ..."
    ready=0
    for _ in $(seq 1 90); do
        if curl -fsS "${SERVER_URL}/health" >/dev/null 2>&1; then
            ready=1
            break
        fi
        if ! kill -0 "$WHISPER_SERVER_PID" 2>/dev/null; then
            echo "ERROR: whisper-server exited during startup -- check the log above." >&2
            exit 1
        fi
        sleep 3
    done
    if [ "$ready" -ne 1 ]; then
        echo "ERROR: whisper-server did not become ready in time." >&2
        exit 1
    fi
    echo "whisper-server ready."
fi

exec python3 /app/local_audio_service.py
