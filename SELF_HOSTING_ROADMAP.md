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

## Fixed in the November 2026 follow-up (were open items 1, 2 and 4)

- **`/_local/storage` served recordings to anyone with the link, forever.** Links are now signed
  (`?sig=`, plus `exp=` when the caller asks for an expiry) with a key made on first start in
  `/data/secrets/local_storage_url_key` (or `OMI_LOCAL_STORAGE_URL_KEY`), and the mount refuses
  anything else with 403 (`backend/utils/other/local_storage_urls.py`). App logo and thumbnail
  buckets stay unsigned. Most links are made fresh per request, so few saved links break;
  `OMI_LOCAL_STORAGE_ALLOW_UNSIGNED=1` keeps old ones working while they are re-made. The key is
  not `ENCRYPTION_SECRET`, because `entrypoint.sh` defaults that to a published string.
- **One stranger could lock every tunnel user out of login.** List the cloudflared container in
  `OMI_TRUSTED_PROXY_NETWORKS` and the limiter counts its `CF-Connecting-IP` instead of the
  connector's address. Headers from any other peer are still ignored. The per-username budget is
  unchanged, so someone who knows a username can still lock that one account for 15 minutes.
- **omi-host tests ran only by hand.** The ones that need only python3 and bash run in the local
  and CI lanes (`omi-host/run_stdlib_tests.sh`, check `omi-host-stdlib-tests`). The audio-service
  tests need fastapi/numpy and still run by hand.

## Open items, priority order

### 0. Installs made before the per-install `ENCRYPTION_SECRET` still use the published one
New installs make their own key on first start and keep it in `/data/secrets/encryption_secret`
(`omi-host/encryption-env.sh`); `ENCRYPTION_SECRET` in `.env` still wins. A data volume that was
used before that change, with nothing set in `.env`, keeps the old published value
(`omi_local_dev_harness_32_byte_test_secret_not_prod`) and warns on every start, because its data
was encrypted under it and a new key would make that data unreadable. What is still missing is a
migration that re-encrypts existing data under a new key. The secret also signs ids and cursors
(sync content ids, capture manifests, page cursors), so those need a plan too. Until then, an old
install is protected by who can reach the data volume and its backups, not by this key.

### 1. Day review: rolled-up conversations and chat retrieval
Rolled-up conversations are discarded (hidden, restorable via *Reopen day*). The day summary itself
is not yet indexed for Ask Omi, so "what happened on Tuesday?" may not see it. Next step: index a
finalized day summary like a conversation (or add a `get_day_review` chat tool).

### 2. Remaining SaaS UI and Firebase packages
Plan & Usage / Stripe pages and the PostHog/Intercom settings entries are still present (inert
without keys). `firebase_crashlytics`/`firebase_messaging` are neutered at call sites but still in
`app/pubspec.yaml`; removing them needs a native-manifest pass and a device build.

### 3. Translations for new UI
Day review, chat shortcuts and folder-tree strings exist in English only (`app_en.arb`); other
locales fall back to English.

### 4. Local analytics/crash reporting, if ever wanted
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

## Analytics removed, cloud features off (November 2026)

- **Third-party analytics removed.** The backend and pusher no longer ship the PostHog SDK; the
  server's product events go to a local usage store in this server's own Firestore
  (`backend/database/local_usage.py`): per-day counts and each person's newest events, kept
  `OMI_LOCAL_USAGE_RETENTION_DAYS` (90) days, off with `OMI_LOCAL_USAGE=off`. The admin reads it on
  the dashboard ("Usage on this server", `/dashboard/usage`); each person reads only their own at
  `/v1/users/me/local-usage`. LangSmith tracing is ignored when self-hosted. The web share pages
  lost Google Analytics, the PostHog capture and the Elfsight widget; `web/app` lost posthog-js.
- **Billing off.** Self-hosted, `/v1/users/me/subscription` answers an unlimited plan with the plan
  UI hidden, the desktop chat quota is unlimited, and checkout/portal/plan changes answer 404.
- **Cloud STT off while local STT is configured** (`omi-host/audio-env.sh`).
- **Still open (app side, after the UI pass):** the Flutter app's own analytics calls, paywall
  screens and cloud-only settings.
