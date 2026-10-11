#!/usr/bin/env bash
# Makes sure the whisper.cpp ggml model is on disk, then hands off to
# local_audio_service.py. This script does NOT start whisper-server any more:
# local_audio_service.py owns that process -- started on the first ASR call and
# stopped after OMI_ASR_IDLE_UNLOAD_S idle -- because stopping the process is
# the only thing that releases the model's VRAM (whisper.cpp's server exposes
# /inference, /load and /health, but no /unload). VAD, diarization, WS
# streaming, and TTS all still run in-process in that service, unchanged.
#
# Set OMI_ASR_ENGINE=fasterwhisper to skip whisper.cpp entirely and use the
# original CPU faster-whisper path instead (local_audio_service.py loads that
# model itself in that case).
set -euo pipefail

# MIOpen's kernel find-db (resemblyzer/torch GPU diarization) needs this to
# exist before the first conv runs, so the one-time benchmarking pass
# (MIOPEN_FIND_ENFORCE=3) has somewhere to persist its results across restarts.
mkdir -p "${MIOPEN_USER_DB_PATH:-/data/miopen/gfx906}"

if [ "${OMI_ASR_ENGINE:-whispercpp}" = "whispercpp" ]; then
    WHISPER_MODEL="${WHISPER_MODEL:-large-v3-turbo}"
    MODEL_DIR="${WHISPER_CPP_MODELS_DIR:-/data/whispercpp/models}"
    MODEL_FILE="${MODEL_DIR}/ggml-${WHISPER_MODEL}.bin"
    SERVER_BIN=/app/whisper.cpp/build/bin/whisper-server
    SERVER_URL="${WHISPER_CPP_SERVER_URL:-http://127.0.0.1:8081}"

    # whisper-server is NOT started here any more. local_audio_service.py owns
    # the process: it starts it on the first ASR call and stops it once the
    # model has been idle for OMI_ASR_IDLE_UNLOAD_S, restarting it on the next
    # call. Stopping the process is the only thing that actually releases the
    # ggml model's VRAM -- whisper.cpp's server exposes just /inference, /load
    # and /health, so the /unload this used to rely on never existed and the
    # model was pinned in VRAM for the container's whole lifetime.
    #
    # We keep the download so the model is present for that first call.
    mkdir -p "$MODEL_DIR"
    if [ ! -f "$MODEL_FILE" ]; then
        echo "Downloading ggml model '${WHISPER_MODEL}' into ${MODEL_DIR} ..."
        /app/whisper.cpp/models/download-ggml-model.sh "${WHISPER_MODEL}" "${MODEL_DIR}"
    else
        echo "Model cache hit: ${MODEL_FILE}"
    fi
    echo "whisper-server will be started on demand by the audio service (${SERVER_URL})."
fi

exec python3 /app/local_audio_service.py
