# Audio routing for the omi-local backend. Sourced by entrypoint.sh; also sourced
# directly by omi-host/test_audio_env.py.
#
# The backend sends all audio to one service speaking Omi's audio protocol: live
# WebSocket, batch transcription, speaker embeddings and TTS. That service is
# omi-audio (local_audio_service.py). Two settings move things around:
#
#   OMI_AUDIO_URL   where that service is. Default: the bundled omi-audio container.
#                   Point it at another machine running the same image, or set it to
#                   "off" for a cloud-only setup (needs a cloud STT key below).
#   *_API_KEY       cloud STT providers the backend speaks natively (Modulate,
#                   Soniox, Deepgram). Used only without a local audio service
#                   (OMI_AUDIO_URL=off) or in an explicit STT_SERVICE_MODELS: with
#                   local STT configured, cloud STT is off and the keys are dropped.
#
# Which engine omi-audio itself transcribes with (its own whisper.cpp, or any
# OpenAI-compatible server or frontier API) is set on the omi-audio container
# (OMI_ASR_ENGINE, OMI_ASR_URL, ...). Worked examples: omi-host/examples/audio/.

OMI_AUDIO_URL="${OMI_AUDIO_URL:-http://omi-audio:8790}"
if [ "$OMI_AUDIO_URL" != "off" ]; then
  # Each surface can still be pointed somewhere else on its own.
  export HOSTED_PARAKEET_API_URL="${HOSTED_PARAKEET_API_URL:-$OMI_AUDIO_URL}"
  export HOSTED_SPEAKER_EMBEDDING_API_URL="${HOSTED_SPEAKER_EMBEDDING_API_URL:-$OMI_AUDIO_URL}"
  export TTS_LOCAL_BASE_URL="${TTS_LOCAL_BASE_URL:-$OMI_AUDIO_URL}"
fi

# Cloud STT is a cloud-only feature: with a local audio service configured and no
# explicit chain, vendor keys are dropped so audio never leaves this server.
if [ -n "${HOSTED_PARAKEET_API_URL:-}" ] && [ -z "${STT_SERVICE_MODELS:-}" ]; then
  for _cloud_stt_key in MODULATE_API_KEY SONIOX_API_KEY DEEPGRAM_API_KEY; do
    if [ -n "${!_cloud_stt_key:-}" ]; then
      echo "audio: local STT configured, ignoring $_cloud_stt_key (cloud STT is off)"
      unset "$_cloud_stt_key"
    fi
  done
  unset _cloud_stt_key
fi

# Live-STT serving chain. Upstream's default is modulate-velma-2,soniox,dg-nova-3,parakeet,
# and the backend refuses to start when soniox is listed without SONIOX_API_KEY
# (utils/stt/streaming.py validate_streaming_stt_env). List only the cloud engines
# whose key is set, in upstream's order, ahead of the local audio service.
if [ -z "${STT_SERVICE_MODELS:-}" ]; then
  STT_SERVICE_MODELS=""
  [ -n "${MODULATE_API_KEY:-}" ] && STT_SERVICE_MODELS="${STT_SERVICE_MODELS}modulate-velma-2,"
  [ -n "${SONIOX_API_KEY:-}" ] && STT_SERVICE_MODELS="${STT_SERVICE_MODELS}soniox,"
  [ -n "${DEEPGRAM_API_KEY:-}" ] && STT_SERVICE_MODELS="${STT_SERVICE_MODELS}dg-nova-3,"
  [ -n "${HOSTED_PARAKEET_API_URL:-}" ] && STT_SERVICE_MODELS="${STT_SERVICE_MODELS}parakeet,"
  STT_SERVICE_MODELS="${STT_SERVICE_MODELS%,}"
  if [ -z "$STT_SERVICE_MODELS" ]; then
    echo "error: OMI_AUDIO_URL=off needs at least one of MODULATE_API_KEY, SONIOX_API_KEY, DEEPGRAM_API_KEY" >&2
    exit 1
  fi
  export STT_SERVICE_MODELS
fi

# Upstream keeps the plain live Parakeet slot off unless STT_STREAMING_PARAKEET_SLOT_ENABLED
# is on (config/live_stt_gpu_fleet.py). The local audio service is that slot here, so turn
# it on whenever the live chain lists parakeet; an explicit value still wins.
case ",${STT_SERVICE_MODELS}," in
  *,parakeet,*)
    export STT_STREAMING_PARAKEET_SLOT_ENABLED="${STT_STREAMING_PARAKEET_SLOT_ENABLED:-true}"
    ;;
esac

# Recordings transcribed after the fact (sync uploads) try the local service first,
# then Modulate. Without a local service only Modulate can serve them.
if [ -z "${STT_PRERECORDED_MODEL:-}" ] && [ -z "${HOSTED_PARAKEET_API_URL:-}" ]; then
  if [ -n "${MODULATE_API_KEY:-}" ]; then
    export STT_PRERECORDED_MODEL=modulate-velma-2
  else
    echo "warning: no local audio service and no MODULATE_API_KEY: synced recordings can't be transcribed" >&2
  fi
fi

echo "audio: ${HOSTED_PARAKEET_API_URL:-off} | live STT chain: ${STT_SERVICE_MODELS}"
