# omi-windows (omi-local)

Omi for Windows and Linux — an Electron + React + TypeScript port of the Omi
desktop app, reworked for **omi-local**: it signs in to *your own* omi-local
server and talks to nothing else. No Omi cloud, no Firebase project, no
analytics or crash reporting. Linux specifics: [LINUX.md](LINUX.md).

## Recommended IDE Setup

- [VSCode](https://code.visualstudio.com/) + [ESLint](https://marketplace.visualstudio.com/items?itemName=dbaeumer.vscode-eslint) + [Prettier](https://marketplace.visualstudio.com/items?itemName=esbenp.prettier-vscode)

## Run from source

Requires Node 22.19+ (CI pins Node 22, matching `package.json`'s `engines.node`
range; Node 24+ breaks the jsdom test suites — see `scripts/check-node-version.mjs`).
With [nvm](https://github.com/nvm-sh/nvm) installed, `nvm use` in this directory
picks up the pinned version from `.nvmrc` automatically.

This directory is pnpm-managed — running `npm install` instead will corrupt
`package.json`/`pnpm-lock.yaml`/`pnpm-workspace.yaml` (npm doesn't understand
pnpm-workspace semantics) and leave a stray, untracked `package-lock.json`
behind. If you see unexplained diffs in those three files with no matching
commit, this is almost certainly why — `git restore` them and reinstall with
pnpm.

CI pins pnpm to major version **10**. If your system `pnpm --version` is a
different major (e.g. 8 or 11+), `.npmrc`'s `node-linker=hoisted` setting can
be silently ignored, breaking postinstall with a confusing "closure
package(s) do not resolve on disk" error — use `npx pnpm@10 <command>`
instead of downgrading a system-managed pnpm install.

```bash
# 1. Install dependencies
nvm use   # or: nvm install (first time)
pnpm install --frozen-lockfile

# 2. Start the app
pnpm run dev
```

No `.env` is needed: nothing about the server is baked into the build.
`.env.example` lists the few optional settings.

### Linux (Wayland compositors)

On native Wayland compositors with limited XWayland support (e.g. niri),
`pnpm dev` can fail to map the main window at all — the tray icon appears but
no window does. Set `OMI_OZONE=wayland` to run under native Wayland instead
(global shortcuts and active-window detection won't work in that mode). If
the window still comes up blank rather than missing, also add
`OMI_DEV_HW_GPU=1`. See [docs/multi-worktree-dev.md](docs/multi-worktree-dev.md)
for the full dev-only environment variable reference and parallel-worktree
port/profile isolation.

`pnpm run dev` automatically unsets `ELECTRON_RUN_AS_NODE` for the spawned Electron
app. Some shell/tooling sessions leave that variable set after using Electron as a
Node runtime; if it leaks into app startup, Electron does not expose `electron.app`
and the dev app crashes before opening.

## Sign-in: your omi-local server

The first screen asks for the **server address** (e.g. `http://<server-ip>:8000`
on the LAN, or your tunnel URL), a **username** and a **password** — the account
the server's owner made with `backend/scripts/seed_local_account.py`. The app posts
them to the server's `POST /v1/auth/local-login` and keeps the session token it
gets back (30 days, signed by your server; encrypted at rest with the OS keychain
via Electron safeStorage). Every API call then sends `Authorization: Bearer
<token>` to that server.

- Change the server in **Settings → Account → Server** (a different server signs
  you out). `OMI_SERVER_URL=<url>` pins it for an install.
- The main process owns the server URL and the session (`src/main/omiLocal/`);
  windows read them at load and are told about every change.
- Plans, billing, the paywall and the "Omi Pro" upsell are hidden — there are none
  on omi-local.

## omi-local behavior

- **Waiting for your OK** (`/approvals`, reached from the Home chip): tasks,
  heard calendar events and day summary drafts the server holds until you
  approve; Approve all (with Undo), per-item approve/decline, and the day review
  read by section with "Approve the day". Tasks also marks waiting rows.
- **Slow local models**: a conversation the server is still processing shows
  "Writing notes…" and the list re-polls; chat says "Still working…" after 12 s
  and waits up to 20 minutes (4× the backend's default 300 s
  `OMI_LOCAL_LLM_TIMEOUT_SECONDS`) before giving up.
- The UI is otherwise upstream's; it gets a full redesign later.

## What leaves this machine

Only requests to **your server**. The renderer's Content-Security-Policy is built
by the main process from the configured server (`src/main/omiLocal/csp.ts`): pages
may connect to that server and loopback, nothing else.

- Removed: Sentry (main and every renderer), PostHog, Firebase Auth/SDK (and its
  Google token/cert endpoints), the api.omi.me / Cloud Run API bases, the
  api.omi.me update-feed resolver, h.omi.me share links. Errors and product /
  fallback events are written to the local log only (`userData/logs`).
- Hosts the app can still contact, and only when you act:
  - your server (API, chat, `/v4/listen` microphone stream, WebSockets);
  - your own update feed, if you set `OMI_UPDATE_FEED_URL` (see Updates);
  - Settings → Integrations you connect yourself: Gmail (`mail.google.com`,
    `accounts.google.com` in an Omi-owned window), Google OAuth for calendar
    (`accounts.google.com`, `www.googleapis.com`), Notion export (`api.notion.com`);
  - keys you enter yourself (BYOK "Test"): `api.openai.com`, `api.anthropic.com`,
    `generativelanguage.googleapis.com`, `api.deepgram.com`, `openrouter.ai`;
  - coding agents you connect (Claude Code via `claude.com`/`platform.claude.com`
    sign-in, Codex via `api.openai.com`), which then talk to their own vendors;
  - sign-language translation, if you switch it on (General; off by default):
    `us-central1-sign-mt.cloudfunctions.net`;
  - "Use omi memory anywhere" opens `chatgpt.com`, `claude.ai` or
    `gemini.google.com` in your browser when you click it;
  - images (conversation/app icons) load from wherever the server's data points
    (`img-src https:`).
  Links in About/Agents (`omi.me`, `help.omi.me`, `github.com`, docs) only open in
  your browser when clicked.

## Coding agents (Claude Code, OpenClaw, Hermes, Codex)

Omi can delegate tasks to external coding agents over ACP (Agent Client
Protocol). Name an agent in chat or push-to-talk — *"ask Codex to fix the
failing test in my omi repo"*, *"use Claude Code to add a readme"* — and Omi
hands the task over, streaming the agent's progress into the conversation. If
the agent you named fails to start, Omi falls back to the next connected one;
if it isn't connected at all, the reply tells you how to set it up.

Coding agents are yours, not Omi's: each one talks to its own vendor with your
credentials.

- **Claude Code** ships built in (the `@agentclientprotocol/claude-agent-acp`
  bridge, spawned as a Node child process) — no separate install. It uses your
  Claude sign-in (`claude` CLI credentials or `ANTHROPIC_API_KEY`).
- **OpenClaw / Hermes / Codex** are external CLIs you install yourself, then
  connect in **Settings → Agents** by saving a launch command
  (e.g. `openclaw acp`, `hermes acp`, `npx @agentclientprotocol/codex-acp`).
  The **Test** button runs a real ACP handshake against the command.
  Power users can instead set `OMI_OPENCLAW_ADAPTER_COMMAND` /
  `OMI_HERMES_ADAPTER_COMMAND` / `OMI_CODEX_ADAPTER_COMMAND` in the
  environment; a Settings command takes precedence when both exist.

External agents run with a minimal allowlisted environment (host secrets are
never forwarded) and never receive automatic permanent permission grants.
The working directory for a task is an explicit path in your message, else the
indexed folder matching a "in my X repo" hint, else your most recently active
indexed folder. Adapter code lives in `src/main/codingAgent/`.

## Build

```bash
# Windows
pnpm run build:win

# macOS
pnpm run build:mac

# Linux
pnpm run build:linux
```

The build carries no service configuration; the server is chosen at sign-in.
Windows installers can only be built on Windows (NSIS + the .NET OCR/automation
helpers); the Linux build is covered in [LINUX.md](LINUX.md).

## Updates

omi-local has no update server, so by default the app **never checks for
updates**. To update from builds you host, set `OMI_UPDATE_FEED_URL` to an
electron-builder "generic" feed: a directory served over http(s) with the
`latest.yml` (Windows) / `latest-linux.yml` (AppImage) and installers from
`dist/`. Windows (NSIS) and Linux AppImage installs then check it every 4 hours;
a `.deb` is updated by the package manager.

## Verify your changes

```bash
pnpm typecheck   # tsc, node + web configs
pnpm lint        # ESLint (blocking in CI; Prettier formatting is not)
pnpm test        # vitest, ~550 tests, runs against an Electron stub
```

## Floating bar

The always-on-top bar window has several non-obvious Windows pathologies (OS
show-fade, clip-reveal requirement, orb WebGL blink, eaten hardware clicks). Read
[docs/bar-gotchas.md](docs/bar-gotchas.md) **before** changing bar window logic or
animations — it also documents the fast verification loop (harness scripts,
`[bar-diag]` logging).

## Conversation sync

Screen-session (mic + system audio) recordings sync to your omi-local server via
`POST /v1/conversations/from-segments` with a client-owned outbox (offline
retry, duplicate-safe). Design, outbox semantics, and the live E2E harness
(`pnpm test:e2e:conv-sync`) are documented in
[docs/conversation-sync.md](docs/conversation-sync.md).
