# Audio: where transcription runs

**Default: nothing to do.** The stack is self-contained. The `omi-audio` container
transcribes with its own whisper.cpp on the GPU, tells speakers apart, and speaks
replies with Piper. No audio leaves the machine. Everything in this folder is
optional: copy a recipe only if you want transcription to run somewhere else.

## How audio flows

```
phone ──► omi-local (backend) ──► omi-audio (gateway) ──► engine
                                   live stream, VAD          whisper.cpp (bundled, default)
                                   diarization, voice IDs    or any OpenAI-compatible
                                   batch uploads, TTS           /v1/audio/transcriptions
```

The backend doesn't call a speech engine directly. It speaks Omi's own audio
protocol, and `omi-audio` is the service that answers it:

| Endpoint | What the backend uses it for |
|---|---|
| `WS /v3/stream` | live transcript while you talk |
| `POST /v2/transcribe` | recordings synced from the device later, with speakers |
| `POST /v2/embedding` | voice fingerprints, used for speaker identification and your speech profile |
| `POST /v1/tts` | spoken replies |

No off-the-shelf speech server offers these endpoints. That is why `omi-audio` stays
in front as a **gateway**. It cuts the audio into utterances (VAD), sends each
finished utterance to the **engine** as an ordinary OpenAI
`POST /audio/transcriptions` request, and adds back timestamps and speaker labels.
Swapping the engine therefore changes only who turns sound into words. Speaker
identification, speech profiles and synced recordings keep working.

## The two settings

| Setting | Read by | Means |
|---|---|---|
| `OMI_ASR_ENGINE`, `OMI_ASR_URL`, `OMI_ASR_MODEL`, `OMI_ASR_API_KEY` | `omi-audio` | **which engine** transcribes: `whispercpp` (default), `fasterwhisper` (CPU), or `openai` (any URL) |
| `OMI_AUDIO_URL` | `omi-local` | **where the gateway is**: the bundled container (default), another machine, or `off` |

Both go in `omi-host/.env`. That is the file next to `docker-compose.yml`, which
`docker compose` and `run-omi-local-host.sh` both read.

## Pick a recipe

| You want | Use | Audio leaves your network? |
|---|---|---|
| The default (whisper.cpp on this GPU) | nothing | no |
| Another local engine: speaches, LocalAI, a whisper box on another PC | [`external-local-engine.env.example`](external-local-engine.env.example) | no |
| OpenAI (`whisper-1`, `gpt-4o-transcribe`) | [`frontier-openai.env.example`](frontier-openai.env.example) | **yes** |
| Groq hosted Whisper, or any other hosted OpenAI-compatible API | [`frontier-groq.env.example`](frontier-groq.env.example) | **yes** |
| The whole gateway on another machine, none here | [`remote-gateway.env.example`](remote-gateway.env.example) + [`no-bundled-audio.override.yml`](no-bundled-audio.override.yml) here; [`gateway-machine.compose.yml`](gateway-machine.compose.yml) there | no |
| No local audio at all, cloud STT only (Deepgram / Modulate / Soniox) | [`cloud-stt-only.env.example`](cloud-stt-only.env.example) + [`no-bundled-audio.override.yml`](no-bundled-audio.override.yml) | **yes** |
| Any of these on a host without the AMD gfx906 card | add [`no-gpu.override.yml`](no-gpu.override.yml) | — |

## Applying one

1. **`.env.example` recipes:** copy the uncommented lines into `omi-host/.env` and
   fill in the addresses and keys. Lines set there override the compose defaults.
2. **`.override.yml` files:** save the file as `omi-host/docker-compose.override.yml`.
   If you already have one, paste the `omi-audio:` entry under its `services:` instead.
   Compose merges overrides key by key.
3. **Restart:** run `bash run-omi-local-host.sh`, or run `docker compose up -d` from
   `omi-host/`. Compose recreates any container whose settings changed. If the
   recipe parks `omi-audio`, remove the running container once with
   `docker compose rm -sf omi-audio`.

**Going back to the default:** delete the lines you added to `.env`, remove the
override entry, and run `docker compose up -d` again.

## Checking it worked

```sh
# Which engine the gateway uses, and where it sends audio
curl -s http://127.0.0.1:8790/health
#   {"status":"healthy","asr_engine":"openai","asr_url":"http://…/v1","asr_model":"…",…}

# Where the backend sends audio, and its live-STT order
docker logs omi-local 2>&1 | grep '^audio:'
#   audio: http://omi-audio:8790 | live STT chain: parakeet

# A real transcription through the gateway (any short WAV with speech)
curl -s -F file=@sample.wav -F diarize=true http://127.0.0.1:8790/v2/transcribe

# The engine itself, from the gateway's point of view
docker exec omi-audio sh -c 'curl -s "$OMI_ASR_URL/models"'
```

The dashboard (`http://<host>:8000/`) shows the audio service as up or down. It shows
"not configured" when `OMI_AUDIO_URL=off`. In the phone app, keep
**Settings → Transcription → Omi Parakeet (server)**. That name means "this
server's audio service", whatever engine runs behind it.

## Things to know

- **Privacy and cost.** Frontier and cloud recipes send every utterance to that
  company, and Omi records all day. Check their pricing per audio minute before
  switching.
- **No authentication on port 8790.** `omi-audio` trusts whoever reaches it, and so
  does an engine like speaches unless you give it an `API_KEY`. Keep both on a LAN
  or VPN address, and never port-forward them.
- **Addresses are seen from inside containers.** `localhost` in `OMI_ASR_URL` means
  the `omi-audio` container itself. Use a LAN IP, or a container name on the same
  Docker network.
- **Timestamps.** Engines that answer `verbose_json` (speaches, `whisper-1`, Groq)
  return per-segment times. `json`-only models (`gpt-4o-transcribe`) give one segment
  per utterance. That is fine for live audio, where utterances last a few seconds,
  but timing in synced recordings is coarser. `omi-audio` switches to `json` on its
  own after the first rejection, or you can set `OMI_ASR_RESPONSE_FORMAT=json`.
- **Latency.** Each utterance is one HTTP request. A distant engine adds one round
  trip, and a line appears once you pause, the same as today (`OMI_VAD_MIN_SILENCE_MS`).
- **Language.** `OMI_ASR_LANGUAGE=en` is the default. Set another language code, or
  `auto` to let the engine detect it.
- **GPU memory.** With `OMI_ASR_ENGINE=openai`, nothing loads whisper on this GPU, so
  `OMI_ASR_IDLE_UNLOAD_S` no longer matters. Diarization still runs on
  `OMI_DIA_DEVICE` (default `cuda`). Set it to `cpu` and `omi-audio` holds no VRAM.
- **If the engine is down,** live lines and synced recordings come back empty. The
  reason shows up in `docker logs omi-audio` as `ASR engine request failed`. If you
  have set cloud STT keys, the backend tries those first for live audio. The order is
  printed in the `audio:` log line.
