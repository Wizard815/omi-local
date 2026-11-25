# Local only: how the stack is kept off the internet

The whole Omi stack (phone app, `omi-local`, `omi-proxy`, `omi-audio`) runs on the LAN.
Two layers make sure nothing calls the cloud while it runs. Both are on by default.

| Layer | What it stops | Where |
|---|---|---|
| **Egress guard** (in every Python process) | Any name lookup or connection that isn't loopback or a private LAN address. Cloud metadata (`169.254.169.254`) is always refused. Each attempt is logged with the code that made it. | `egress-guard/sitecustomize.py`, switched on by `OMI_EGRESS_GUARD=block` (compose) |
| **Host firewall** (iptables) | Everything else in the containers: native code, gRPC, the Firestore emulator's JVM, Node, shell downloads. Container traffic may go to RFC 1918 ranges only; the rest is dropped and logged. | `egress-firewall.sh`, run by `run-omi-local-host.sh` as root after the stack is healthy |

The guard also switches uvloop off (uvicorn falls back to the standard asyncio loop), because
uvloop resolves names and opens sockets in native code the guard can't see. It also drops any
`HTTP(S)_PROXY` setting, because a proxy on the LAN would carry requests out past the checks.

## Check it on the host

```bash
bash omi-host/egress-firewall.sh status             # the rules, with packet counters
docker exec omi-local tail -n 20 /data/logs/egress.jsonl   # what the guard refused, and which code asked
dmesg | grep omi-egress-drop                         # what the firewall dropped
docker exec omi-local python3 -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)"
# -> fails with "omi egress guard: example.com is outside the local network"
```

Rerun `bash omi-host/egress-firewall.sh` after a reboot or after recreating containers
(their addresses change). On Unraid, add it to the User Scripts plugin with "At startup of array".

## Letting something out on purpose

| You want | Set |
|---|---|
| Your own cloud key, e.g. OpenAI | `OMI_EGRESS_ALLOW=api.openai.com` and `OMI_EGRESS_FIREWALL=0` (or add the address to `OMI_EGRESS_LAN`) |
| OpenRouter models in the proxy | `OMI_EGRESS_ALLOW=openrouter.ai`, `OMI_EGRESS_FIREWALL=0` |
| A faster-whisper model on the first start of `omi-audio` | `OMI_EGRESS_ALLOW=huggingface.co` once, then remove it |
| Health alerts to a public ntfy.sh / Gotify (a LAN one needs nothing; [HEALTH.md](HEALTH.md)) | `OMI_EGRESS_ALLOW=ntfy.sh`, `OMI_EGRESS_FIREWALL=0` |
| See what would be blocked without blocking it | `OMI_EGRESS_GUARD=log` |

`omi-searxng` (web search, off unless `SEARXNG_ENABLED=1`) is left out of the firewall: querying
search engines is its job. The `omi-apps` profile's plugins (Wikipedia, Open-Meteo and others)
are blocked by both layers; enable them only if you want those calls.

## What it can't stop

- **DNS lookups.** Docker answers containers from the host's resolver, so the *name* of a blocked
  host can still reach your DNS server. Point the host at a resolver that only knows LAN names if
  that matters.
- **IPv6.** The firewall rules are IPv4. Docker networks have no IPv6 unless you enabled it, and the
  script warns if a container has a global IPv6 address.
- **The phone itself.** See below.

## Audit (2026-11-17)

What was checked:
- **Code:** a read of the phone app, the backend and the host stack.
- **A live run:** the backend in both provider modes, with the guard recording every attempt.
  - It covered all 249 parameterless GET endpoints, chat, memory extraction, memories, tasks, search, a voice message and the scheduler.
  - A stand-in for llama.cpp received every LLM call.

**Fixed:**

| Found | Fix |
|---|---|
| tiktoken downloads its vocabulary from `openaipublic.blob.core.windows.net` in every new container. Without it, chat failed outright offline. | Both vocabularies are baked into the image (`TIKTOKEN_CACHE_DIR`). |
| firebase-tools downloads the emulator jar and UI on the first start of every new container. Its npm update check also phones home. | Both are fetched at build time (`firebase setup:emulators:*`). `NO_UPDATE_NOTIFIER=1`. |
| A placeholder `GEMINI_API_KEY` in offline mode made code call Google instead of its local fallback. | Placeholder removed. Screen-activity vector search is off and keyword search is kept. |
| The firmware update check (`api.github.com`), the chat's web-fetch tool, map tiles (`tile.openstreetmap.org`, which reveal the location) and developer webhooks to public hosts went straight out. | Refused by both layers. |
| uvloop let the backend's async HTTP bypass the Python-level checks. | uvloop is off while the guard is on. |
| No network rule existed; every container had a route to the internet. | `egress-firewall.sh`. |
| `Dockerfile.dockerignore` left out `caldav-env.sh`, so a clean build failed. | Added. |
| Library telemetry and model downloads: LangSmith tracing, Hugging Face, Chroma telemetry, and google-auth's metadata probe. | Switched off in `entrypoint.sh`. |
| Server-side product analytics went to PostHog (integration syncs, chats, MCP calls, billing events) when a key was set, and the proactivity flag was read from PostHog. | The PostHog SDK is removed from the backend and pusher. Events go only to the local usage store (`backend/database/local_usage.py`, `OMI_LOCAL_USAGE`); the flag comes from the Omi flag store. LangSmith is ignored when self-hosted, even with a key. |
| Cloud STT keys in `.env` put the vendor ahead of the local engine. | With omi-audio configured, `audio-env.sh` drops `MODULATE_API_KEY`, `SONIOX_API_KEY` and `DEEPGRAM_API_KEY`. |

**Phone app (Android, dev flavor):**

| Found | Fix |
|---|---|
| Firebase Messaging fetched a push token from Google on every launch. | Auto-init and Analytics are off in the manifest. |
| Android verified `h.omi.me` / `try.omi.me` App Links on install. | Those links are removed. |
| The native PostHog SDK came back with an upstream merge. It was idle, but the Gradle code still read the removed `posthog_flutter` plugin, which breaks the build. | Removed. |
| The background audio streamer fell back to `api.omiapi.com` (audio and the session token) when no server URL was saved. | It now falls back to `127.0.0.1:9`, which fails on the phone. |
| App icons and setup instructions were fetched from `raw.githubusercontent.com`. | They no longer are. |

**Left as your choice.** These only happen when you tap them, and none runs on its own:
- **Settings → Transcription → cloud providers.** Your own Deepgram, OpenAI or Gemini key, with your audio.
- **On-device Whisper.** The model downloads once from `huggingface.co`.
- **Links that open the browser:** help, feedback, docs, the privacy page, Google Maps for a conversation's place, and the app stores.
- **Share links.** They use `h.omi.me` unless the APK is built with `--dart-define=OMI_SHARE_BASE_URL=http://<server>:8000`.
- **Connecting an AI agent (MCP).** The key handoff goes through `zkr.scalingforever.com`.
- **Firmware updates.** The server no longer fetches releases from GitHub. Update the device by hand (nRF Connect / the release zip).
- **Android itself.** Location and the system speech or TTS engines are the OS's, not the app's.
