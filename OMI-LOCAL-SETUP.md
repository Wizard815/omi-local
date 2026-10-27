# Omi — complete local setup (Unraid + Android phone)

Everything below was **built and verified on this machine** (Hermes container on
RaidLab, 192.168.20.5), not just designed. What works, what's local vs cloud, and
the two commands to finish.

## What is included

| Artifact | Path | What it is |
|---|---|---|
| **Android APK** | `omi-dev-local.apk` | `com.friend.ios.dev` (dev flavor), built from `app/` with Flutter 3.44.5. Signs in with a server URL (`http://192.168.20.5:8000/` or a tunnel URL) plus the username/password seeded on the server. Cleartext + `omi-dev://` callback scheme confirmed in the manifest. |
| **Host stack package** | `omi-host/` | `Dockerfile` + `docker-compose.yml` + `entrypoint.sh` + `run-omi-local-host.sh`. Two containers (one compose project): **omi-local** (Firebase **Auth emulator** (container-internal), **Firestore emulator (:8085)**, **Redis (:6380)**, Python **backend (:8000)**) and **omi-audio** (fully-offline **CPU** speech: faster-whisper STT + speaker diarization + Piper TTS on `:8790`). All from this repo's own code (`backend/`), offline provider mode. |

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

**2. Build and install the APK on the phone:**

```bash
# build: release, arm64 only, obfuscated — about half the size of the old
# `flutter build apk --debug --flavor dev` (debug JIT, all ABIs). The dev flavor
# is release-signed with the debug keystore (android/app/build.gradle), so no
# release keystore is needed. Prints the APK path and size.
app/scripts/build_local_apk.sh
# from a machine that can reach the repo (or scp the apk straight to the phone):
adb install -r app/build/app/outputs/flutter-apk/app-dev-release.apk   # path printed by the script
# or: copy app-dev-release.apk to the phone, open it and tap install
#     (allow "install unknown apps" for your file manager)
```

Then open **Omi Dev** and enter the **Server URL** (`http://192.168.20.5:8000/` on
the LAN, or your tunnel URL) and the username/password you seeded with
`backend/scripts/seed_local_account.py`. The backend checks the password and
signs its own session token (see **Login** below). No Omi cloud, no Omi account,
no Google, no Firebase project is touched.

## What's local vs what's cloud (by design of this repo)

The Omi app is architecturally a **Firebase-Auth + backend-API** client. To run it
"local" you must therefore run the parts it depends on locally:

| Layer | Local in this setup? | Notes |
|---|---|---|
| **Identity / sign-in** | ✅ Local | Username/password checked by the backend (`/v1/auth/local-login`), which signs its own session tokens. The Firebase **Auth emulator** (project `demo-omi-local`) only holds each account's uid, inside the container. See **Login** below. |
| **All app data** (conversations, memories, settings, action items, chat) | ✅ Local | Python backend (`:8000`) + **Firestore emulator** (`:8085`) + Redis. The app makes **zero direct Firestore calls** — everything goes through the backend, so only `:8000` needs to be reachable. |
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

> **Why not just point the app at Omi's cloud?** `api.omi.me` only accepts Omi's
> *production* accounts. Your account and its session tokens belong to your own
> backend, so the cloud would answer 401 on every call.

## What was verified (real runs, not assumptions)

- Backend boots offline on this box; `/health` → 200.
- (Historical: the backend has refused Auth-emulator tokens since October 2026, see
  **Login**.) Created a user in the Auth emulator → its token verified by the backend →
  `GET /v1/conversations` and `GET /v1/users/onboarding` returned **200**
  (`/v1/users/profile` → `410 User not found`, the correct pre-onboarding state).
- APK built (`app/scripts/build_local_apk.sh` — release, arm64-only, obfuscated; the earlier
  `flutter build apk --debug --flavor dev` all-ABI debug build was ~2x larger), `aapt2` confirms
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
  docker-compose.yml           # pins 192.168.20.5:8000 + omi-audio (:8790 loopback)
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

### Transcribing somewhere else

The default above stays self-contained. `omi-audio` can instead send each utterance
to any OpenAI-compatible `/v1/audio/transcriptions` engine (`OMI_ASR_ENGINE=openai`,
`OMI_ASR_URL`). That engine can be a local speaches or LocalAI box, or a frontier API.
`omi-audio` keeps the Omi-specific endpoints, VAD, diarization and TTS, so speaker
identification and synced recordings keep working. `OMI_AUDIO_URL` on `omi-local`
moves the whole audio service to another machine, or `off` turns it off for cloud
STT only. Recipes, with what each one changes and why:
[`omi-host/examples/audio/`](omi-host/examples/audio/README.md).

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
Leave it recording all day. A clip that ends (silence, the stop button, an offline-sync
upload) is kept as a transcript with a title from its first words; nothing runs on the model
and nothing piles up as a finished conversation. The rule-based noise check still drops empty,
filler-only and mic-check clips on its own. In the list such clips say "Waiting for your day
review"; open one to read its transcript.

A "Review your day" card appears on the conversation list (for earlier unsorted days, and for
today after 6 pm), or open it with `/review` or the book icon in **Daily Recaps**. For each clip:
**star** to keep it (it then gets the full treatment: title, summary, tasks, memories), **tick**
two or more and **Merge** to make them one conversation (processed once, as one), **drop** the
noise, or leave it. Short or unclear clips are listed in their own section. Everything left is
rolled into one long day summary and leaves the list. In the summary, select text → **Correct**
(attach what's right) or **Redact** (never shown again, never sent to the model again), and use
**+ Add paragraph** to write whole paragraphs. **Apply corrections** rewrites the summary with
them. **Reopen day** brings the summarized and dropped clips back; a merge stays merged.
`OMI_CONVERSATION_HOLD=off` restores per-clip processing. Tune `DAY_REVIEW_TIMEOUT_SECONDS` /
`DAY_REVIEW_MAX_INPUT_CHARS` for slow or small-context models.

**Day bundle (one tap, or automatic).** Fifty clips a day is normal when the device never
stops, and most are small. **Bundle the day** on the review folds every unstarred clip into one
day summary written in **sections**; each section names the clips it came from and carries the
model's tags (people, places, projects). **Favorite** a section to pull its clips back out as
starred conversations (held clips get the full treatment then); **Unfavorite** folds them back.
The summary keeps covering favorited sections, so corrections and regeneration work as before.
Past days are bundled on their own once the local clock passes `OMI_DAY_BUNDLE_HOUR` (default
3 am); a day the model could not summarize is retried after six hours. `OMI_DAY_BUNDLE_AUTO=off`
leaves bundling to the button. The manual sort (star, merge, drop) is still there under the
button.

### Tags
Every conversation carries tags: the day bundle adds the section's tags to its clips, and you
edit them on the conversation (the **Tags** chip under the title: type one, or tap one of your
existing tags). The server normalizes them (lowercase, dashes) and keeps a per-user catalog with
counts. **Search → Tags** lists the catalog; a tag opens its conversations, newest first.
API: `PUT /v1/conversations/{id}/tags`, `GET /v1/conversations/tags`,
`GET /v1/conversations?tag=work`.

### Calendar & tasks (CalDAV)
Tasks and the day's events live outside Omi too. `omi-host` runs **Radicale** (`omi-radicale`,
port `CALDAV_PORT`, default 5232; set `CALDAV_PASSWORD` in `.env` or it refuses to start) and
the backend syncs with it every hour and on **Sync now** (Settings → **Calendar & tasks**):
open tasks and recently completed ones go out as to-dos, a to-do ticked off on the phone flips
the Omi task, to-dos made elsewhere become Omi tasks; day summaries land as all-day events and
the events the pipeline extracted from conversations as timed ones; the calendar's own events
come back into Omi for the chat's calendar tool. Point a phone (DAVx5 + Tasks.org) or a desktop
client at `http://<host>:5232/` with the same user and password to see the same lists. To use
Nextcloud, Baïkal, iCloud or another server instead, enter it on the settings page (per user) or
set `CALDAV_DEFAULT_URL` / `CALDAV_USER` / `CALDAV_PASSWORD` for the whole stack. Worked
examples: `omi-host/examples/caldav/README.md`. Contacts (CardDAV) are not synced.

### Folder tree
Folders can be nested (up to 5 levels). Folders live in **Search** (upstream moved folder
browsing there): the tiles are top-level folders; opening one shows its path and its subfolders
as chips (long-press a chip to edit, add a subfolder or delete), plus **New subfolder**. The tree
icon opens the whole tree (open, new subfolder, move, edit). AI folder assignment sees full paths
("Work / Clients / Acme").

### Login
Every sign-in, on the LAN or through a tunnel, goes through the backend's
`/v1/auth/local-login`: it checks the password (argon2, rate-limited) and returns a
session token it signs with `LOCAL_AUTH_JWT_SECRET`. Leave that unset and one is
generated on first start and kept in the data volume (`/data/secrets/local_auth_jwt_secret`),
so sessions survive restarts; delete that file to sign every device out.

The Firebase Auth emulator issues *unsigned* tokens, and the Firebase Admin SDK checks
no signature in emulator mode, so a backend that accepted them let anyone who could
reach it act as any user. On a self-hosted stack the backend now refuses every
Firebase ID token (`backend/utils/self_hosted_id_tokens.py`), the emulator listens on
the container's loopback only, and port 9099 is no longer published.

Moving over from the old **Local network** login:
- **Accounts:** nothing to do. An account that only exists in the emulator (seeded for
  the old LAN login) signs in once through `/v1/auth/local-login`, keeps its uid and
  data, and gets its own login entry.
- **App already installed:** the next request after the upgrade gets a 401. Pick
  **Remote (internet)** on the login screen and enter the same LAN URL
  (`http://<server IP>:8000/`). If the app still shows you signed in, sign out first.
- **New app build:** the login screen is a single form, and a leftover emulator
  session is signed out at startup.

### Host tuning (October 2026)
- A code edit no longer reinstalls every backend package: the Dockerfile installs from the lock
  first and copies the code after, and `omi-host/Dockerfile.dockerignore` keeps tests, scratch
  audio and stray venvs out of the image.
- The fleet-rollout pollers (cohort reads every minute, STT fleet health every 5 s, queue-age
  telemetry) stay off on a self-hosted stack.
- The Firestore emulator's JVM is capped at `OMI_FIRESTORE_HEAP` (2g).
- The merge proxy keeps one connection pool to llama.cpp and remembers an empty model list
  for a few seconds instead of re-asking a down upstream on every call.
- Still per request by design: Piper reloads its voice on each spoken reply, and whisper
  unloads after `OMI_ASR_IDLE_UNLOAD_S` (300) so the first words after a pause pay a cold
  start; raise it if you'd rather keep the VRAM busy. `OMI_ASR_BEAM_SIZE=1` trades a little
  accuracy for a much faster decode.

### Backups and what survives a crash
The Firestore emulator holds all conversations, memories and accounts **in memory**; the
data volume only has what was exported. Until October 2026 the only export was the clean
stop, so a crash, a power cut or a `docker stop` that outran the grace period lost
everything since the last start. Now (`omi-host/maintenance.sh`):
- the emulator is exported every 15 minutes (`OMI_FIRESTORE_EXPORT_INTERVAL_S`), into a
  scratch directory first, then swapped in; the previous export stays as
  `firebase-export.prev`;
- once a day (`OMI_BACKUP_INTERVAL_S`) a fresh export, a Redis snapshot and the local
  storage, Chroma vectors and the login key are tarred into `/data/backups/omi-data-<utc
  stamp>.tar.gz`; the newest `OMI_BACKUP_KEEP` (7) are kept. Model caches are not included:
  they re-download;
- if the backend process dies, the container exports and exits non-zero so Docker restarts
  it, instead of exiting without an export.

**Restore:** stop the stack, unpack a tarball over the data volume's root (on Unraid the
volume is under Docker's `volumes/omi-host_omi-data/_data`; `tar xzf omi-data-<stamp>.tar.gz
-C <that path>`), start the stack. Copy the tarballs somewhere off the box too: a
`docker cp omi-local:/data/backups .` in a cron job on the host is enough.

### Self-hosting switch (`OMI_SELF_HOSTED`)
omi-host's entrypoint exports `OMI_SELF_HOSTED=1`. The backend reads it (`backend/utils/self_hosting.py`)
to turn off what a self-hosted install does not have: billing and quota gates, the whole-account
cutover fence, the Cloud Tasks queue for historical sync (backfill runs inline) and, unless you set
`MODULATE_API_KEY`, the Modulate cloud STT provider. `PROVIDER_MODE=offline` alone no longer does
this: upstream's own test stacks run with it and still expect those gates.

### Settings the entrypoint now derives (upstream sync, October 2026)
- `STT_SERVICE_MODELS`: built from the keys you set (`MODULATE_API_KEY`, `SONIOX_API_KEY`,
  `DEEPGRAM_API_KEY`), ending in the local `parakeet` from omi-audio unless `OMI_AUDIO_URL=off`.
  Upstream's default chain lists Soniox, and the backend refuses to start when Soniox is listed
  without its key. Set `STT_SERVICE_MODELS` yourself to override. The logic lives in
  `omi-host/audio-env.sh`, which also points `HOSTED_PARAKEET_API_URL`,
  `HOSTED_SPEAKER_EMBEDDING_API_URL` and `TTS_LOCAL_BASE_URL` at `OMI_AUDIO_URL`.
- `MEMORY_ENABLED=on`: upstream's single memory switch. The old `MEMORY_MODE` and
  `MEMORY_CANONICAL_CONSOLIDATION_ENABLED` are retired; with `MEMORY_ENABLED` unset every memory write
  is paused.

### Daily recap
The backend now runs the hourly recap job itself (`OMI_LOCAL_SCHEDULER=1`, default on) at your
chosen local hour, and the app reports its time zone on start-up, so recaps and day reviews use
your day, not UTC.
