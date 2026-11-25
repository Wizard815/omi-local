# Omi on Linux (omi-local)

Linux is a **platform seam on `desktop/windows`**, not a separate tree: one
Electron codebase builds both the Windows and the Linux app, and only OS-specific
adapters branch on `process.platform`. Like the Windows build, it talks to **your
own omi-local server and nothing else** (see README → "What leaves this machine").

Status (November 2026): builds, unit tests pass on Linux, the unpacked Linux build
(`electron-builder --linux --dir`) launches under Xvfb, signs in to an omi-local
server and shows conversations, tasks, "Waiting for your OK" and chat. AppImage
and .deb packaging is configured (below) but was not produced in the verification
sandbox for lack of disk space (the pi-mono/Claude agent closure makes the unpacked
app ~1.4 GB, and the AppImage step needs about twice that free).

## Run from source

```bash
cd desktop/windows
pnpm install --frozen-lockfile      # pnpm 10, Node 22.19+ (see AGENTS.md)
pnpm run dev                        # X11 session, or XWayland (see Wayland below)
```

No `.env` is needed. On first start the app asks for your **server address**
(e.g. `http://<server-ip>:8000`), **username** and **password** — the account
`backend/scripts/seed_local_account.py` made on the server. `OMI_SERVER_URL=...`
pins the server for an install (kiosk/managed setups, test harnesses).

## Build

```bash
pnpm run build:linux                # AppImage + .deb into dist/
# or only an unpacked directory (fast, no packaging tools needed):
pnpm run build && npx electron-builder --linux --dir --config electron-builder.config.mjs --publish never
```

Free disk: about 1.5 GB for the unpacked app, about 3.5 GB for an AppImage build.

## Runtime dependencies (Debian/Ubuntu)

```bash
sudo apt-get install -y x11-utils tesseract-ocr tesseract-ocr-eng libnotify4 libxss1
# tray icon on GNOME: the AppIndicator extension (gnome-shell-extension-appindicator)
```

- `x11-utils` provides `xprop` — active-window detection (app usage, the OCR
  helper's window info).
- `tesseract-ocr` (+ `eng`) backs screen OCR ("what's on my screen"). Without it
  screen reading degrades gracefully; the rest of the app is unaffected.
- The `.deb` depends on `tesseract-ocr`, `tesseract-ocr-eng`, `libnotify4`,
  `libxss1`, `x11-utils` and recommends `libappindicator3-1` and
  `xdg-desktop-portal`. AppImage users install the packages above themselves.
- Headless/CI: run under `xvfb-run` and pass `--no-sandbox`.

## Microphone (PipeWire / PulseAudio)

Chromium (and so Electron) records through the **PulseAudio API**. On a PipeWire
desktop (Fedora, Ubuntu 22.10+, Arch with pipewire) that needs the
`pipewire-pulse` shim, which those desktops install by default.

- Check: `pactl info` should print a `Server Name` (`PulseAudio (on PipeWire …)`
  or `pulseaudio`). If `pactl` is missing or fails, install `pipewire-pulse` (or
  `pulseaudio`) and log in again.
- Omi uses the **default input**. Pick it in your desktop's sound settings, or
  `pactl list short sources` then `pactl set-default-source <name>`.
- There is no OS permission prompt on a normal desktop: Electron grants the
  microphone to the app itself. Sandboxed packaging (Flatpak/Snap — not shipped)
  would need the portal's microphone permission instead.
- What you say streams to **your server** (`/v4/listen` for live capture,
  `/v2/voice-message/transcribe-stream` for push-to-talk), which hands it to its
  local speech service (`omi-audio`). Nothing goes to a cloud speech service.
- Meeting capture of *system* audio uses Chromium's
  `PulseaudioLoopbackForScreenShare` (enabled on Linux); without a Pulse layer it
  is inert and capture is mic-only.

## Tray

The tray uses PNG icons on Linux (`resources/tray/*.png` + `@2x`; nativeImage
cannot read the Windows `.ico` files there). KDE and most wlroots bars show it
directly; **GNOME needs the AppIndicator extension**. Many Linux trays never
deliver a left click and open the menu instead — everything is in the menu (Open
Omi, Settings, Quit). Closing the window keeps Omi running in the tray; Quit is
in the tray menu.

## Launch at login

Settings → General → Launch at login writes
`~/.config/autostart/omi-local.desktop` (`$XDG_CONFIG_HOME` honored), which starts
the app — the AppImage itself when run from one — with `--hidden` (tray only).
GNOME, KDE, Xfce, Cinnamon, MATE and LXQt run XDG autostart entries; niri, Sway
and Hyprland need an autostart runner (`dex -a`, or systemd's
`xdg-autostart-generator` via `systemctl --user` sessions). Only packaged builds
offer the switch (in dev the executable is the bare Electron binary).

## Wayland vs X11

The app targets X11. On a Wayland session it defaults to **XWayland**
(`ozone-platform=x11`), because native Wayland breaks Electron global shortcuts
(push-to-talk, overlay summon) and the X11 active-window path. On compositors
with limited XWayland support (niri, Sway, Hyprland — detected from each one's
session marker, see `src/main/linuxCompositor.ts`) it defaults to native Wayland,
since XWayland can fail to map the main window there. `OMI_OZONE=x11` or
`OMI_OZONE=wayland` overrides the choice either way.

| | X11 / XWayland | native Wayland |
|---|---|---|
| Global shortcuts (push-to-talk, bar) | yes | no — use the tray's "Toggle OMI Bar" |
| Active window (app usage) | yes (`xprop`) | no |
| Screen capture | direct | desktop portal, asks "Share screen?" each time |
| Continuous Rewind capture | on by default | off by default (the portal would prompt every frame); on-demand "what's on my screen" still works |

## Updates

There is no Omi update server. A `.deb` never updates itself (use your package
manager). An **AppImage** checks for updates only if you host a feed:
`OMI_UPDATE_FEED_URL=https://your-host/omi-desktop/` pointing at a directory with
`latest-linux.yml` and the AppImage from `dist/`. Unset (the default), the app
never contacts an update host.

## What works / what's next

- ✅ Sign-in to the omi-local server, conversations (incl. "Writing notes…"),
  tasks with approvals, "Waiting for your OK" with the day review, chat with slow
  local models ("Still working…", 20-minute ceiling).
- ✅ Tray (PNG), launch at login (XDG autostart), screen OCR (Tesseract helper).
- ✅ Mic → your server's local speech service (needs `omi-audio` running there).
- ⚠️ App-usage tracking: X11 active-window via `linuxForeground.ts`, unit-tested,
  not runtime-smoked under Xvfb in CI.
- ⚠️ Voice hub (realtime voice): it asks the server for an OpenAI/Gemini realtime
  session; omi-local answers 503, so it stays off. Push-to-talk transcription
  works through the server.
- ⏳ Pendant BLE, Glass video, native-Wayland capture (portal restore-token).

## Implementation notes

- `src/main/usage/linuxForeground.ts` — X11 active-window (xprop + /proc/<pid>/exe).
- `src/main/usage/nativeForeground.ts` — Linux branch delegates to the above; the
  Windows (koffi) path is unchanged. koffi is lazy-`require`d.
- `src/main/usage/foregroundMonitor.ts` — starts on `win32` and `linux`; Linux is
  poll-only.
- `resources/linux-ocr-helper/omi-ocr-helper` — Node-script helper speaking the
  `ocr/helperProtocol.ts` frame protocol, backed by `tesseract` and `xprop`/`/proc`;
  spawned with Electron's own Node (`ELECTRON_RUN_AS_NODE=1`).
- `src/main/linuxAutostart.ts` — the XDG autostart entry.
- `src/main/tray.ts` — PNG icons off Windows.
- `electron-builder.config.mjs` — Linux targets are AppImage + deb (no snap:
  strict confinement blocks `xprop`/`tesseract`/`/proc`).
