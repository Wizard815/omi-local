# Self-Hosting Roadmap

Running log of what's been fixed toward a fully self-hosted, zero-Google/zero-cloud
deployment ("Omi Local"), and what's still open. Two audit passes fed this file —
see git history on this file for how it grows over time.

## Fixed in the first passes (September 2026)

- **Login not persisting across app restarts** — root cause: `app/lib/providers/auth_provider.dart`'s
  Firebase `idTokenChanges()` listener wiped the valid session token on every cold start,
  because a self-hosted remote-login session never creates a Firebase user by design, so
  Firebase correctly (but wrongly-consequentially) reported `user == null` every time. Fixed
  by skipping the clear when `isLocalRemoteSession` is true.
- **Backfill sync (recordings uploaded after-the-fact from the wearable) permanently 503'd**
  — `backend/routers/sync.py` hard-failed backfill-lane jobs without real Google Cloud Tasks,
  even when BYOK (the actual reason that gate exists) wasn't in play. Now falls through to
  the existing local "inline" dispatch path when there's no BYOK key involved.
- **`local_dev` build profile rejected any public API host** — blocked using a Cloudflare
  Tunnel domain at all. Added an explicit `OMI_LOCAL_DEV_TRUSTED_HOST` dart-define
  (`app/lib/env/env.dart`, wired from `app/setup.sh`) so a build can declare one trusted
  public host instead of loosening the check for every `local_dev` build.
- **Google Sign-In removed** from the login screen (`app/lib/pages/onboarding/auth.dart`).
- **Firebase Crashlytics neutered** — every call site (`crashlytics_manager.dart`,
  `main.dart`'s error handlers, `logger.dart`'s talker observer) is now a no-op. No crash/log
  data reaches Google. Package still in `pubspec.yaml` (see Open Items).
- **FCM push-token registration neutered** (`notification_service_fcm.dart`) — no server on
  either end can use an FCM token anyway; local notifications are unaffected.
- **Gemini embeddings call** (`backend/utils/llm/clients.py`, `gemini_embed_query`) now only
  reaches Google when a Gemini key is actually configured; otherwise routes through the same
  local embeddings client the rest of the RAG pipeline already uses.
- **GCS fallback for local storage** — `OMI_HARNESS_STATE_ROOT`/`OMI_LOCAL_STORAGE_ROOT` are
  now actually set in `omi-host/docker-compose.yml`, so `backend/utils/other/storage.py`
  uses real local disk instead of falling through to `google.cloud.storage.Client()` for
  buckets like `private_cloud_sync`.
- **Dashboard phone-setup instructions were stale** (`backend/routers/dashboard.py`) — told
  users to sign in with Google (removed), called the app "Omi Dev" (renamed "Omi Local"),
  and said transcription "routes to local CPU ASR" when the default engine is GPU-accelerated
  (ROCm). Rewritten to match reality.
- **Chat-tool app registration didn't invalidate the app-browser cache**
  (`backend/scripts/register_chat_apps.py`) — newly registered self-hosted apps (Wikipedia,
  Open Library, etc.) wouldn't show up in the Android app for up to 10 minutes.

## Fixed in later passes (were open items)

- **Transcription allowance capped at 0s offline** — `resolve_transcription_allowance` now has the
  same `PROVIDER_MODE == 'offline'` bypass as `enforce_chat_quota` (e67325d).
- **Account-cutover fence black-screening the app** — fails open on offline deployments (ce2d763).
- **SaaS clutter** — referral page, store-rating nag removed; fair-use page reworded (72d463a).
- **Desktop PostHog phoning home** — off by default on macOS and Windows (80a6248).

## Fixed in the October 2026 audit

Each item is its own commit with a regression test; see `git log` on the branch.

- **Dashboard model allowlist never reached the merge proxy** (`omi-host/merge_proxy.py`,
  `docker-compose.yml`) — the proxy looked for Redis at host `redis` (no such service; Redis runs
  inside `omi-local:6380`) and pinged it once at import, before `omi-local` was even up, then gave
  up for good. Now points at `omi-local`, connects lazily, falls back to `MODEL_ALLOW` per read.
  Also: upstream 4xx/5xx on a streamed chat now keep their HTTP status instead of arriving as the
  body of a 200 event stream (the backend's retry/fallback keys on the status).
- **Ask Omi answered only with the canned apology when no separate chat model was picked** —
  `chat_agent` resolved to `""` when the dashboard's chat model was unset; it now means "same as
  primary".
- **Device-calendar chat tool never found events** — the tool asked the *model* to supply the user
  id; events lived in a dict wiped by every backend restart (now Redis, 7-day TTL); and the app
  posted to `//v1/device-calendar/sync` (404) while reporting success. Also fixed the layering
  violation (`utils` importing `routers`) that crashed 6 backend test files on import.
- **No automatic daily recap, and recaps in UTC** — nothing ran upstream's hourly Cloud Scheduler
  job here; the backend now runs it in-process (`OMI_LOCAL_SCHEDULER=1`, default on). And with FCM
  registration disabled the app never told the backend its time zone; it now calls
  `PUT /v1/users/time-zone` instead.
- **"Open Server Dashboard" buttons 404'd** (`//` and `//dashboard/models`) — same double-slash
  base-URL join as the calendar sync.
- **Repo checks that were already failing on `main`** — the OpenAPI contract runner could not
  import the app (`argon2` missing from `openapi-requirements.txt`); 12 self-hosting routes had no
  `route_policy_manifest.yaml` entry; the app analyzer ratchet failed on
  `device_calendar_sync.dart`; and the fair-use ARB edit was never regenerated into the Dart
  localizations, so the app still showed the SaaS text.

## Open items, priority order

### 1. `/_local/storage` serves files without authentication
`backend/main.py` mounts the local blob store (`StaticFiles`) on the same port a Cloudflare Tunnel
exposes, and `local_storage.py`'s `generate_signed_url()` returns plain, non-expiring paths. Paths
contain random Firebase uids and conversation UUIDs (no directory listing), so this is
capability-by-obscurity, not open browsing — but any leaked URL (logs, a shared screenshot) is a
permanent link to a recording. Recommended fix: HMAC-sign `generate_signed_url()` with an expiry
(`?exp=&sig=`, key from `ENCRYPTION_SECRET`) and verify it in a thin ASGI wrapper in front of
`StaticFiles`, keeping `public_url` (stored long-term for app logos/thumbnails) unsigned for those
buckets only. Not changed in this pass because URLs already stored in Firestore would break.

### 2. Local-login lockout through the tunnel
`utils/local_auth.py` keys its attempt budget on `request.client.host`. Behind cloudflared every
internet request has the connector's IP, so five bad attempts from anyone lock *everyone* out for
the lockout window (and the per-username axis lets anyone lock your account). Options: trust
`CF-Connecting-IP` only when the TCP peer is the cloudflared container, or raise the window.

### 3. Day review: rolled-up conversations and chat retrieval
Rolled-up conversations are discarded (hidden, restorable via *Reopen day*). The day summary itself
is not yet indexed for Ask Omi, so "what happened on Tuesday?" may not see it. Next step: index a
finalized day summary like a conversation (or add a `get_day_review` chat tool).

### 4. omi-host tests are not in any CI lane
`omi-host/test_merge_proxy_stream.py` and `test_audio_idle_unload.py` only run by hand. Register
them in `.github/checks-manifest.yaml` (local + ci lanes).

### 5. Remaining SaaS UI and Firebase packages
Plan & Usage / Stripe pages and the PostHog/Intercom settings entries are still present (inert
without keys). `firebase_crashlytics`/`firebase_messaging` are neutered at call sites but still in
`app/pubspec.yaml`; removing them needs a native-manifest pass and a device build.

### 6. Translations for new UI
Day review, chat shortcuts and folder-tree strings exist in English only (`app_en.arb`); other
locales fall back to English.

### 7. Local analytics/crash reporting, if ever wanted
- **Analytics**: self-hosted PostHog — the SDK is already wired in (`analytics_manager.dart`,
  `product_telemetry.py`), just point `POSTHOG_HOST` at your own instance.
- **Crash reporting**: self-hosted Sentry — a real SDK swap (`sentry_flutter` instead of
  `firebase_crashlytics`), not a redirect.
- **Push notifications**: UnifiedPush (e.g. self-hosted `ntfy`) is the closest open standard,
  but the app already runs a persistent foreground service for recording, so push-to-wake
  isn't actually needed the way it would be for a typical app.

## Confirmed already safe (no action needed)
Google Cloud Tasks (defaults to local inline dispatch), Deepgram/Soniox/Modulate STT (require
a key, raise cleanly if absent), Stripe (backend blocks any call with no key), OpenAI/OpenRouter/
Anthropic/Gemini chat LLMs (all BYOK-gated; default path is the local llama-swap proxy), Google
OAuth sign-in flow (button removed), no hardcoded `api.omi.me` fallback anywhere in the app.
