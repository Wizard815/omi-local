# Desktop (Windows/Linux) against omi-local

Screenshots of the Electron app in `desktop/windows/`, taken on Linux (Xvfb,
1280x800) with Playwright driving Electron, against a running omi-local backend
(Redis + Firestore/Auth emulators + `backend/` uvicorn, the same pieces
`omi-host` runs). The account and data were seeded with
`omi-host/demo/seed_day.py` (`account`, `conversations`, `tasks`, `day_draft`)
plus one conversation left in `status: processing`. Chat ran through
`omi-host/local_llm_adapter.py`; for 009 a proxy in front of the model added 30 s
to every model call to stand in for a slow local model.

| File | What it shows |
|---|---|
| 001 | **Before.** The only sign-in was Apple/Google on Omi's cloud Firebase project; no way to reach a self-hosted server. |
| 002 | **Before.** Even with the API base rebuilt to the local server, the static renderer CSP blocked it ("Network Error"). |
| 003 | Sign in to your own server: address, username, password (`/v1/auth/local-login`). |
| 004 | Home shows "N waiting for your OK" (the Hub has no nav rail). |
| 005 | Conversations: `status: processing` shows "Writing notes…" and re-polls. |
| 006 | Tasks: waiting tasks are marked and can be approved or declined in place. |
| 007 | "Waiting for your OK": day summary draft (read by section, Approve the day) and tasks. |
| 008 | After Approve all: exactly what was approved, with Undo; the day is now approved. |
| 009 | Chat while the model is slow: "Still working…" instead of an error (ceiling 20 min). |
| 010 | A chat answer from the omi-local server. |
| 011 | Settings → Account: the server address (Test / Switch server) and who is signed in. |
| 012 | The packaged Linux build (`electron-builder --linux`, linux-unpacked) signed in. |

Network during every run: the renderer and the whole Electron session contacted
only `http://127.0.0.1:8400` (the server) and the app's own loopback renderer
origin (recorded with `session.webRequest.onCompleted`).
