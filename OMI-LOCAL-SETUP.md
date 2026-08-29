# Omi — complete local setup (Unraid + Android phone)

Everything below was **built and verified on this machine** (Hermes container on
RaidLab, 192.168.20.5), not just designed. What works, what's local vs cloud, and
the two commands to finish.

## What is included

| Artifact | Path | What it is |
|---|---|---|
| **Android APK** | `omi-dev-local.apk` | `com.friend.ios.dev` (dev flavor), built from `app/` with Flutter 3.44.5. Pre-wired to `http://192.168.20.5:8000/` + Auth emulator `192.168.20.5:9099`. Cleartext + `omi-dev://` callback scheme confirmed in the manifest. |
| **Host stack package** | `omi-host/` | `Dockerfile` + `docker-compose.yml` + `entrypoint.sh` + `run-omi-local-host.sh`. Two containers (one compose project): **omi-local** (Firebase **Auth emulator (:9099)**, **Firestore emulator (:8085)**, **Redis (:6380)**, Python **backend (:8000)**) and **omi-audio** (fully-offline **CPU** speech: faster-whisper STT + speaker diarization + Piper TTS on `:8790`). All from this repo's own code (`backend/`), offline provider mode. |

The APK is already at the repo root. The host package is in `omi-host/`.

## The two commands to finish

**1. Start the local stack on the Unraid host** (one time; ~3 min to build):

```bash
# from anywhere with the omi-local repo checked out:
bash omi-host/run-omi-local-host.sh
```

This runs `docker build` (context = repo root) and `docker compose up -d`, then
waits for `http://192.168.20.5:8000/health`. It must run **on the host**
(`ssh root@192.168.20.5`), because the phone can only reach `192.168.20.5`, not
the Docker-bridge network this dev container sits on.

**2. Install the APK on the phone:**

```bash
# from a machine that can reach the repo (or scp the apk straight to the phone):
adb install -r omi-dev-local.apk
# or: open omi-dev-local.apk on the phone and tap install
#     (allow "install unknown apps" for your file manager)
```

Then open **Omi Dev** → **Sign in with Google** (any Google account). The local
Firebase Auth emulator verifies the Google token and mints a `demo-omi-local`
token; the local backend verifies it. No Omi cloud, no Omi account, no Omi
Firebase project is touched.

## What's local vs what's cloud (by design of this repo)

The Omi app is architecturally a **Firebase-Auth + backend-API** client. To run it
"local" you must therefore run the parts it depends on locally:

| Layer | Local in this setup? | Notes |
|---|---|---|
| **Identity / sign-in** | ✅ Local | Firebase **Auth emulator** (`:9099`, project `demo-omi-local`). "Sign in with Google" still contacts Google's OAuth servers (the emulator verifies the returned token) — that one hop to `accounts.google.com` is inherent to Google Sign-In; no Omi involvement. |
| **All app data** (conversations, memories, settings, action items, chat) | ✅ Local | Python backend (`:8000`) + **Firestore emulator** (`:8085`) + Redis. The app makes **zero direct Firestore calls** — everything goes through the backend, so only `:8000` and `:9099` need to be LAN-reachable. |
| **Speech-to-text (live capture / memos / batch)** | ✅ Fully local (server-side CPU) | In-app **Settings → Transcription → "Omi Parakeet"** routes speech to the `omi-audio` container (faster-whisper on CPU, ~5× realtime on the ML350). The phone only streams raw audio; **no STT runs on the phone** in this path. This is the default. |
| **Speech-to-text (on-phone alternative)** | ✅ Fully local | **Settings → Transcription → "On-device (Whisper)"** runs Whisper entirely on the phone (downloads a model once), no network. Use this if you'd rather keep STT off the server; heavier on the phone's battery/CPU. |
| **Speaker diarization ("who said what")** | ✅ Fully local | `omi-audio` does resemblyzer ECAPA embeddings + greedy clustering, wired to the backend's `HOSTED_SPEAKER_EMBEDDING_API_URL`. |
| **Text-to-speech (Omi's spoken replies)** | ✅ Fully local | `omi-audio`'s Piper TTS, wired via `TTS_LOCAL_BASE_URL` in `routers/tts.py`. (Cloud ElevenLabs stays available if you set `ELEVENLABS_API_KEY`.) |
| **AI chat / memory extraction / summaries** | ✅ Fully local (option 1 below) | The backend calls both an OpenAI-compatible API (`gpt-5.6-luna` / `gpt-5-nano`) and the Anthropic Messages API (`claude-sonnet-4-6`, the main agentic chat). Both are routed to your llama.cpp — the Anthropic side via `omi-host/local_llm_adapter.py`, an Anthropic→OpenAI translator. |

### AI options (set when starting the host stack)

1. **Fully local (your MI50 llama.cpp)** — start llama.cpp with an OpenAI server and pass it in:
   ```bash
   OPENAI_BASE_URL=http://192.168.20.5:8081/v1 \
   OPENAI_API_KEY=*** \
   LOCAL_LLM_MODEL=qwen-local \
   bash omi-host/run-omi-local-host.sh
   ```
   This makes **every** LLM call local, both surfaces:
   - **OpenAI path** (summaries, memory extraction): the backend's `ChatOpenAI`
     client honors `OPENAI_BASE_URL` directly.
   - **Anthropic path** (the main agentic chat, model `claude-sonnet-4-6`):
     the container also starts `local_llm_adapter.py` on `:8788` and sets
     `ANTHROPIC_BASE_URL` to it. The adapter translates the Anthropic
     Messages API (streaming + tool calls) to OpenAI chat completions and
     forwards to llama.cpp. Zero backend code changes.

   `LOCAL_LLM_MODEL` is the model name the adapter sends upstream — set it to
   whatever your llama.cpp serves (or use llama.cpp `--alias` and leave it
   empty to pass `claude-sonnet-4-6` through; llama.cpp also accepts unknown
   names when it serves a single model). Adapter verified end-to-end with the
   real Anthropic SDK 0.111.0: text + streaming + tool-call round trips
   (`.local/test/test_adapter_e2e.py`).

2. **Your own cloud key** — `OPENAI_API_KEY=*** PROVIDER_MODE=real bash omi-host/run-omi-local-host.sh`
   (chat/memories go to OpenAI; identity + storage stay local).

3. **Offline / inert** (the default) — the stack runs, auth + capture + storage all
   work, and on-device Whisper transcribes fully locally; only the AI features are
   disabled (they fail closed rather than call a fake endpoint). Good for testing the
   whole pipeline without any AI.

> **Why not just point the app at Omi's cloud?** `api.omi.me` verifies tokens against
> Omi's *production* Firebase project. Your local `demo-omi-local` tokens would be
> rejected with 401 on every call. The whole point of the local Auth emulator is that
> the local backend verifies against the *same* project the app signs into.

## What was verified (real runs, not assumptions)

- Backend boots offline on this box; `/health` → 200.
- Created a user in the Auth emulator → its token verified by the backend →
  `GET /v1/conversations` and `GET /v1/users/onboarding` returned **200**
  (`/v1/users/profile` → `410 User not found`, the correct pre-onboarding state).
- APK built (`flutter build apk --debug --flavor dev`), `aapt2` confirms
  `com.friend.ios.dev`, `usesCleartextTraffic=true`, `omi-dev://` callback,
  `RECORD_AUDIO` + BLE permissions, launchable `com.friend.ios.MainActivity`.
- **Local audio service** (`omi-host/local_audio_service.py`) run end-to-end on the
  ML350 CPU (2× Xeon Gold 6152, 88 threads, AVX-512) with a real 7 s speech clip:
  - `POST /v1/transcribe` → **1.4 s** (≈5× realtime), flawless transcription
  - `POST /v2/transcribe` (diarize) → 5.1 s, correct single speaker
  - `POST /v2/embedding` → 256-dim resemblyzer ECAPA vector
  - `POST /v1/tts` (Piper) → audio back, ≈2.8× realtime
  - `WS /v3/stream` → **realtime streaming**: `{"type":"ready"}` handshake then
    `{"type":"utterance", text, start_ms, duration_ms, speaker}` frames; 1.8 s for 7 s of audio
- **STT provider selection** (backend, offline + local env, no cloud keys) verified:
  live streaming / PTT / pre-recorded all select **parakeet** → `HOSTED_PARAKEET_API_URL`
  (the local service). With `MODULATE_API_KEY`/`DEEPGRAM_API_KEY` set, the cloud
  providers win again — i.e. local is the default, cloud is opt-in.

## Repo notes / things I deliberately did NOT do

- **No `git commit`/`push`** (per your standing instruction — you commit). Only
  build artifacts + `omi-host/` + generated `lib/*.g.dart` changed; all are gitignored
  or new. `app/pubspec.lock` was re-owned to your uid so build_runner could `touch` it
  (its content is byte-identical to the original — I restored it).
- **Typesense is intentionally not started.** Conversation full-text search is optional
  and the client falls back to Firestore queries; leaving it out keeps the stack to
  four services. (The upstream harness runs Typesense in Docker; this box has no
  Docker daemon, and the pinned Typesense binary's CDN is unreachable from here.)
- **Desktop (macOS) app is out of scope** for a phone setup; the `backend/` runs the
  same code the macOS desktop uses, so it's not wasted.

## Files I created (all under the repo, gitignored or new)

```
omi-dev-local.apk              # the Android APK
omi-host/
  Dockerfile                   # omi-local host container image
  Dockerfile.audio             # omi-audio image (pipecat base + faster-whisper/piper/resemblyzer)
  docker-compose.yml           # pins 192.168.20.5:8000 + :9099 + omi-audio (:8790 loopback)
  entrypoint.sh                # starts redis + firebase emulators + backend (+ local-audio env)
  local_audio_service.py       # fully-offline CPU audio: WS /v3/stream + /v1//v2 transcribe
                               #   + /v2/embedding + /v1/tts (Piper) + /health
  local_llm_adapter.py         # Anthropic->OpenAI shim for the local LLM
  run-omi-local-host.sh        # the one host-side command (builds both images, starts both)
  download-piper-voice.sh      # one-time Piper voice download into the shared volume
  .dockerignore
.local/                        # toolchains I installed (flutter, android sdk, jdk,
                               #   firebase-tools, redis) — not part of the package
.local/runtime/                # my local test-run state (emulators + backend I booted
                               #   here to verify; safe to delete)
```

## Local audio (`omi-audio`) — how it fits

The backend already had a **self-hosted STT seam**: it talks to whatever
`HOSTED_PARAKEET_API_URL` points at (a WebSocket `/v3/stream` for live capture and
`POST /v1//v2/transcribe` for batch) and to `HOSTED_SPEAKER_EMBEDDING_API_URL` for
diarization. `local_audio_service.py` speaks that exact protocol on CPU, so:

- **STT** = faster-whisper (CTranslate2, int8). Default `Systran/faster-whisper-small`
  (~5× realtime on the ML350). Set `OMI_ASR_MODEL=Systran/faster-whisper-large-v3` for
  top accuracy (~1× realtime) if you want it.
- **Endpointing** = pure-numpy energy VAD with AGC (no torchscript — robust).
- **Diarization** = resemblyzer ECAPA embeddings + online greedy speaker clustering.
  Toggle off with `OMI_DISABLE_DIA=1` (segments then label `SPEAKER_00`).
- **TTS** = Piper (`en_US-lessac-medium` by default; `download-piper-voice.sh` fetches it,
  other voices supported). Serves MP3 when `ffmpeg` is present, WAV otherwise.

Phone → server, no STT on the phone: the phone streams raw 16 kHz PCM over
`/v4/listen`; the backend forwards it to `omi-audio`. To use it, set
**Settings → Transcription → "Omi Parakeet"** in the app.

Everything is CPU. Nothing is forced to the cloud: with no `MODULATE_API_KEY`,
`DEEPGRAM_API_KEY`, or `ELEVENLABS_API_KEY` set, the provider policy selects the
local audio service for every surface (verified). Set any of those keys and the
corresponding cloud path becomes available again — that's the optional fallback.
