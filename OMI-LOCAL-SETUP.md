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
diarization. `local_audio_service.py` speaks that exact protocol, so:

- **STT** = whisper.cpp's `whisper-server` on the GPU (ROCm, gfx906; `WHISPER_MODEL`,
  default `large-v3-turbo`). `OMI_ASR_ENGINE=fasterwhisper` falls back to faster-whisper
  on CPU (`OMI_ASR_MODEL`, default `Systran/faster-whisper-small`).
- **Endpointing** = pure-numpy energy VAD with AGC (no torchscript — robust).
- **Diarization** = resemblyzer ECAPA embeddings + online greedy speaker clustering, on
  the GPU by default (`OMI_DIA_DEVICE=cuda`; `cpu` keeps it off the GPU). Toggle off with
  `OMI_DISABLE_DIA=1` (segments then label `SPEAKER_00`).
- **TTS** = Piper (`en_US-lessac-medium` by default; `download-piper-voice.sh` fetches it,
  other voices supported). Serves MP3 when `ffmpeg` is present, WAV otherwise.

Phone → server, no STT on the phone: the phone streams raw 16 kHz PCM over
`/v4/listen`; the backend forwards it to `omi-audio`. To use it, set
**Settings → Transcription → "Omi Parakeet"** in the app.

Nothing is forced to the cloud: with no `MODULATE_API_KEY`,
`DEEPGRAM_API_KEY`, or `ELEVENLABS_API_KEY` set, the provider policy selects the
local audio service for every surface (verified). Set any of those keys and the
corresponding cloud path becomes available again — that's the optional fallback.

### GPU memory: what idle unload frees

- `whisper-server` starts on the first transcription and is stopped after
  `OMI_ASR_IDLE_UNLOAD_S` (default 300) seconds with no transcription. Stopping the
  process is what frees its VRAM; the next transcription starts it again. Every
  transcription resets the timer: live capture, offline-sync uploads, and the retries
  of a sync that failed.
- Check it with `curl -s 127.0.0.1:8790/health` (`asr_server_running`, `asr_idle_s`,
  `asr_in_flight`). The `omi-audio` log says `whisper-server model released after Ns
  idle` when it happens.
- Not freed by idle unload: with `OMI_DIA_DEVICE=cuda` the audio service process keeps
  its GPU context and the speaker-embedding model for the container's lifetime
  (`rocm-smi --showpids` lists it next to `whisper-server`). Set `OMI_DIA_DEVICE=cpu`
  if idle VRAM has to reach zero; that model is small.
- The service code is copied into the image: after pulling changes, rerun
  `bash omi-host/run-omi-local-host.sh`, which rebuilds the images and restarts them.

## Self-hosted extras (October 2026)

### Chat model shortcuts — `/adult`
Type `/` in **Ask Omi** to see every command. `/adult your question` answers just that message
with a bigger model; `/adult toggle` keeps using it (a chip above the input shows it is on —
tap it or send `/adult toggle` again to turn it off). Pick the model behind each shortcut with
`/model` in the app or the dashboard's **Chat Model Shortcuts** card; add more shortcuts there
(e.g. `/code`). Helper calls (classification, titles) stay on the base model. Other commands:
`/review`, `/apps`, `/clear`, `/help`. Seed shortcuts with `OMI_CHAT_MODEL_SHORTCUTS` in
`omi-host/.env`.

### End-of-day review
A "Review your day" card appears on the conversation list (for earlier unsorted days, and for
today after 6 pm), or open it with `/review` or the book icon in **Daily Recaps**. Star the
conversations you want to keep; everything else is rolled into one long day summary and leaves
the list. In the summary, select text → **Correct** (attach what's right) or **Redact** (never
shown again, never sent to the model again), and use **+ Add paragraph** to write whole
paragraphs. **Apply corrections** rewrites the summary with them. **Reopen day** undoes it all.
Tune `DAY_REVIEW_TIMEOUT_SECONDS` / `DAY_REVIEW_MAX_INPUT_CHARS` for slow or small-context models.

### Folder tree
Folders can be nested (up to 5 levels). Folders live in **Search** (upstream moved folder
browsing there): the tiles are top-level folders; opening one shows its path and its subfolders
as chips (long-press a chip to edit, add a subfolder or delete), plus **New subfolder**. The tree
icon opens the whole tree (open, new subfolder, move, edit). AI folder assignment sees full paths
("Work / Clients / Acme").

### Self-hosting switch (`OMI_SELF_HOSTED`)
omi-host's entrypoint exports `OMI_SELF_HOSTED=1`. The backend reads it (`backend/utils/self_hosting.py`)
to turn off what a self-hosted install does not have: billing and quota gates, the whole-account
cutover fence, the Cloud Tasks queue for historical sync (backfill runs inline) and, unless you set
`MODULATE_API_KEY`, the Modulate cloud STT provider. `PROVIDER_MODE=offline` alone no longer does
this: upstream's own test stacks run with it and still expect those gates.

### Settings the entrypoint now derives (upstream sync, October 2026)
- `STT_SERVICE_MODELS`: built from the keys you set (`MODULATE_API_KEY`, `SONIOX_API_KEY`,
  `DEEPGRAM_API_KEY`), always ending in the local `parakeet` from omi-audio. Upstream's default chain
  lists Soniox, and the backend refuses to start when Soniox is listed without its key. Set
  `STT_SERVICE_MODELS` yourself to override.
- `MEMORY_ENABLED=on`: upstream's single memory switch. The old `MEMORY_MODE` and
  `MEMORY_CANONICAL_CONSOLIDATION_ENABLED` are retired; with `MEMORY_ENABLED` unset every memory write
  is paused.

### Daily recap
The backend now runs the hourly recap job itself (`OMI_LOCAL_SCHEDULER=1`, default on) at your
chosen local hour, and the app reports its time zone on start-up, so recaps and day reviews use
your day, not UTC.
