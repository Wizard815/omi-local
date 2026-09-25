# Self-Hosting Roadmap

Running log of what's been fixed toward a fully self-hosted, zero-Google/zero-cloud
deployment ("Omi Local"), and what's still open. Two audit passes fed this file —
see git history on this file for how it grows over time.

## Fixed this session

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

## Open items, priority order

### 1. Verify: transcription allowance may silently cap local STT at 0 seconds
`backend/utils/subscription.py` (`resolve_transcription_allowance`) decides whether the app
uses the local whisper.cpp/ROCm pipeline (wired in via the "Parakeet" provider slot) or falls
back to weak on-device phone transcription. Unlike a similar chat-quota check elsewhere, this
one has **no offline/self-hosted bypass** — since there's no Stripe subscription, it may report
0 seconds of "managed" transcription remaining. Not confirmed to be on the live recording path,
but check **Settings → Plan & Usage** in the app for a 0-seconds/subscription-required state.
If it's there, add the same `PROVIDER_MODE == 'offline'` bypass `enforce_chat_quota` already has.

### 2. Account-cutover gate can black-screen the whole app on a backend hiccup
`app/lib/services/account_cutover/*.dart` + `backend/routers/account_cutover.py` — a leftover
mechanism for migrating real Based Hardware users between backend generations. Safe today
(absent cutover state or a pure network failure both fall back to "allow"), but it fails
*closed* (full-screen "Migration in progress" blocker) on a malformed response or an explicit
`unavailable` from the backend — plausible during a self-hosted DB restart. No permanent
opt-out exists. Recommend hardcoding `allowProductTraffic = true` for this deployment, or
stripping the gate entirely.

### 3. Settings/UI clutter — safe to ignore, worth stripping eventually
None of these send data anywhere by default or block functionality; they're leftover SaaS UI
that makes no sense for a single self-hosted user:
- Referral page (`app/lib/pages/referral/referral_page.dart`) — opens `affiliate.omi.me` when tapped.
- App Store/Play Store rating nag (`app/lib/services/app_review_service.dart`).
- Plan & Usage / Stripe billing UI (`usage_page.dart`, `plans_sheet.dart`, `payments_page.dart`,
  `stripe_connect_setup.dart`, `cancel_subscription_sheet.dart`) — backend already blocks any
  real Stripe call with no key configured, so these just open dead webviews.
- "Fair Use Policy" usage-limit page (`fair_use_page.dart`) — the whole fair-use subsystem is
  off by default (`FAIR_USE_ENABLED=false`), so this page can only ever show an empty state.
- "Allow us to train on your recordings" consent dialog (`plans_sheet.dart`) — meaningless
  when nobody but you has the data.
- PostHog/Intercom UI entries (`settings_drawer.dart`) — already inert (no keys configured),
  but worth pruning so a future accidental key doesn't quietly re-enable telemetry.

### 4. Firebase Crashlytics/FCM packages still compiled into the APK
Neutered at the call-site level (item above), but `firebase_crashlytics`/`firebase_messaging`
remain in `app/pubspec.yaml`. Fully removing them requires touching native Android manifest
service declarations (FCM) — deferred pending a dedicated build/verify pass, since it wasn't
low-risk enough to do inline with everything else this session.

### 5. Local analytics/crash reporting, if ever wanted
Not needed for a single self-hosted user, but noted for reference:
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
