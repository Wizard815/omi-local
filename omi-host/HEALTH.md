# Health page and alerts

`omi-health` is a small watchdog for the stack. Every minute it checks each part of the
stack and shows the results on a page on your LAN. When something goes down, slows down
or comes back, it tells your phone through a notification server you run yourself
(ntfy or Gotify) or through a webhook. Nothing goes to a cloud push service unless you
point it at one.

## Turn it on

In `omi-host/.env`:

```
OMI_HEALTH_ENABLED=1
OMI_HEALTH_PORT=8099
OMI_HEALTH_NAME=omi                                  # shown in alert titles
OMI_HEALTH_PUBLIC_URL=http://<omi-host>:8099/        # tapping an alert opens the page
```

Then run `bash omi-host/run-omi-local-host.sh`, or just the watchdog:
`docker compose --profile health up -d omi-health`. Open `http://<omi-host>:8099/`.

The watchdog runs from the omi-local image (only Python's standard library), with read-only
access to the data volume and the backup folder.

## Reading the status page

The banner at the top is the worst state of any check. Problems sort to the top. Each row
shows:

| State | Means |
|---|---|
| **ok** (green) | Answering normally. |
| **degraded** (yellow) | Working, but needs a look: slow, still loading models, a queue backing up, low disk, an old backup. |
| **down** (red) | Not answering, or answering with an error. |
| **off** (grey) | Not configured on this server (no Hermes, cloud STT only, no GPU tools). Never alerts. |

The small print gives the detail, the response time and since when the state has held.
The page reloads itself every 30 seconds and follows your device's light or dark mode.
`/status.json` has the same data for scripts and Home Assistant (a REST sensor on
`overall`).

| Check | How | ok / degraded / down |
|---|---|---|
| Backend API | `GET /health`, then `/ready` | answers / `/ready` fails (Redis, from the backend's side) or slower than `OMI_HEALTH_SLOW_MS` / no answer |
| Firestore emulator | `GET :8085/` | answers "Ok" / slow / no answer |
| Auth emulator, Vector store | the backend's `/dashboard/components` (the Auth emulator only listens inside omi-local) | up / dashboard login missing / down |
| Redis | `PING` on omi-local:6380 | `PONG` / - / anything else |
| CalDAV (Radicale) | `PROPFIND /.well-known/caldav`, logged in with the server's password | 207 / password rejected / no answer |
| Audio / STT | omi-audio's `/health` | ready / loading models, or `OMI_HEALTH_AUDIO_MAX_IN_FLIGHT` (4) transcriptions queued / no answer |
| LLM (merge proxy) | the proxy's `/health`, which asks every model server live | all up / one of several down, no models, or slower than `OMI_HEALTH_LLM_SLOW_MS` (5 s) / none answers |
| LLM adapter | omi-local:8788 `/health` | answers / - / no answer |
| Hermes | `HERMES_API_URL/health`, when set | answers / slow / no answer |
| Disk: data, Disk: backups | free space on `/data` and the backup folder | / under `OMI_HEALTH_DISK_WARN_PCT` (15 %) / under `OMI_HEALTH_DISK_CRIT_PCT` (5 %) |
| GPU memory | `nvidia-smi`, when present | / above `OMI_HEALTH_GPU_WARN_PCT` (95 %) / the tool fails |
| Last backup | the newest `omi-backup-*.json` in the backup folder | / older than `OMI_HEALTH_BACKUP_MAX_AGE_H` (26 h), warnings, or a failed test restore / older than twice that, or none |

The GPU check stays off in the container. The MI50's `rocm-smi` is not in the image, and
omi-audio's `/health` already shows whether its model is loaded (`asr_server_running`).

## When an alert is sent

Only on a **change**: down, degraded, recovered. A check stuck in "down" does not repeat.

- **Confirmation.** A check must fail `OMI_HEALTH_FAIL_AFTER` (2) times in a row
  before it counts as down or degraded. With the default 60-second interval, one slow
  answer pages nobody, and a real outage is reported within about two minutes.
- **Cooldown.** After a problem alert for a check, the next problem alert for that same
  check waits `OMI_HEALTH_COOLDOWN_S` (15 minutes). A service that flaps every minute sends
  one "down" and one "recovered", not thirty. If it is still down when the cooldown ends,
  you hear about it then. Getting worse (degraded to down) is sent at once.
- **Recovered** is sent at once, with how long the problem lasted, but only for a problem
  you were told about.
- **Daily digest** (off by default): `OMI_HEALTH_DIGEST_AT=08:00` sends "all good" or the
  list of problems once a day, in `TZ`.

Example alerts (the titles are what your phone shows):

```
[omi] Redis DOWN          Redis is down: [Errno 111] Connection refused
[omi] Redis recovered     Redis is ok again after 2 min (down). PONG in 1 ms
[omi] Last backup degraded   Last backup is degraded: 28.8 h ago, 812 MB
```

## Set up ntfy (self-hosted)

[ntfy](https://ntfy.sh) is one small server plus a phone app. Run it on the server or on
any machine on the LAN:

```
docker run -d --name ntfy --restart unless-stopped -p 8090:80 \
  -v /mnt/user/appdata/ntfy:/var/cache/ntfy binwiederhier/ntfy serve \
  --cache-file /var/cache/ntfy/cache.db --base-url http://192.0.2.30:8090
```

1. In `.env`: `OMI_HEALTH_NTFY_URL=http://192.0.2.30:8090` and `OMI_HEALTH_NTFY_TOPIC=omi-alerts`.
2. Install the ntfy app on your phone (F-Droid or Play Store). Add a server
   `http://192.0.2.30:8090` and subscribe to `omi-alerts`. The F-Droid build needs no Google
   services and keeps its own connection.
3. To keep others on the LAN from reading or posting: enable ntfy's access control
   (`auth-file`, `auth-default-access: deny-all`). Create a user and a token with
   `ntfy token add <user>`, and set `OMI_HEALTH_NTFY_TOKEN=tk_...`.
4. Test it: `docker exec omi-health /app/backend/.venv/bin/python /app/omi-host/health/watchdog.py --test-alert`.

Priorities: down = 5 (urgent), degraded = 4, recovered = 3, digest = 2. In the app you can
let only 4 and up make a sound.

To reach your phone away from home, the phone needs to reach ntfy: over your VPN
(WireGuard, Tailscale), or by publishing ntfy through your tunnel. The watchdog itself
only ever talks to the LAN address.

## Set up Gotify

[Gotify](https://gotify.net) is the same idea with a web UI and an Android app:

```
docker run -d --name gotify --restart unless-stopped -p 8070:80 \
  -v /mnt/user/appdata/gotify:/app/data gotify/server
```

1. Open `http://192.0.2.31:8070`, log in (admin/admin, then change it), go to *Apps → Create
   Application* and name it "Omi". Copy its **application** token. A client token will not work.
2. In `.env`: `OMI_HEALTH_GOTIFY_URL=http://192.0.2.31:8070` and `OMI_HEALTH_GOTIFY_TOKEN=<that token>`.
3. Install the Gotify app, log in with a user. Alerts arrive with priority 8 (down),
   5 (degraded), 4 (recovered), 2 (digest).
4. Test with `--test-alert` as above.

## A webhook (Home Assistant, n8n, your own script)

`OMI_HEALTH_WEBHOOK_URL` gets a JSON `POST` per alert. `OMI_HEALTH_WEBHOOK_TOKEN`, if set,
is sent as `Authorization: Bearer <token>`.

```json
{
  "source": "omi-health", "event": "down", "check": "redis", "label": "Redis",
  "state": "down", "previous": "ok", "detail": "[Errno 111] Connection refused",
  "title": "[omi] Redis DOWN", "message": "Redis is down: [Errno 111] Connection refused",
  "since": "2026-11-25T18:40:12Z", "duration_s": 4, "at": "2026-11-25T18:40:16Z",
  "host": "omi", "status_url": "http://192.0.2.10:8099/"
}
```

`event` is `down`, `degraded`, `recovered`, `digest` or `test`. For Home Assistant, create an
automation with a *Webhook* trigger (`/api/webhook/omi-health`) and send the message to
your phone from there.

You can configure several channels at once. Each alert goes to all of them. A delivery
is retried 3 times. If it still fails, the status page lists it under *Alerts* as FAILED,
and the watchdog carries on.

## The egress guard and alert targets

The watchdog's Python runs under the egress guard ([NETWORK.md](NETWORK.md)), and
`omi-health` is on the host firewall's list. Both allow private LAN addresses, so an
ntfy or Gotify on your LAN works with no extra setting. A host name works too if it
resolves to a LAN address. To use a public server (ntfy.sh), allow it on purpose:
`OMI_EGRESS_ALLOW=ntfy.sh` and `OMI_EGRESS_FIREWALL=0` (or that address in `OMI_EGRESS_LAN`).
Your alert titles then leave the house.

## Settings

| Setting | Default | |
|---|---|---|
| `OMI_HEALTH_ENABLED` | off | `1`: `run-omi-local-host.sh` starts `omi-health` |
| `OMI_HEALTH_PORT` | `8099` | Status page port on `BIND_IP` |
| `OMI_HEALTH_NAME` | `omi` | In alert titles |
| `OMI_HEALTH_PUBLIC_URL` | | Link in alerts |
| `OMI_HEALTH_INTERVAL_S` | `60` | Seconds between rounds |
| `OMI_HEALTH_FAIL_AFTER` | `2` | Failures in a row before down/degraded |
| `OMI_HEALTH_COOLDOWN_S` | `900` | Minimum gap between problem alerts per check |
| `OMI_HEALTH_DIGEST_AT` | off | `HH:MM` daily summary |
| `OMI_HEALTH_NTFY_URL`, `_TOPIC`, `_TOKEN` | | ntfy |
| `OMI_HEALTH_GOTIFY_URL`, `_TOKEN` | | Gotify (application token) |
| `OMI_HEALTH_WEBHOOK_URL`, `_TOKEN` | | Generic JSON webhook |
| `OMI_HEALTH_BACKUP_MAX_AGE_H` | `26` | Backup age before "degraded" |
| `OMI_HEALTH_DISK_WARN_PCT` / `_CRIT_PCT` | `15` / `5` | Free space thresholds |
| `OMI_HEALTH_<CHECK>_URL` | compose names | Override where a check looks: `BACKEND`, `FIRESTORE`, `CALDAV`, `AUDIO`, `LLM`, `LLM_ADAPTER`, `HERMES`; `OMI_HEALTH_REDIS=host:port`. `off` turns that check off. |

If you protect the dashboard with `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD`, compose
passes them to `omi-health` as well, so it can read the Auth emulator and vector store
status. Without them, those two rows show "dashboard login needed".

## Try it without a real outage

`omi-host/demo/backup_demo.sh` ends by running the watchdog against a throwaway stack.
It kills Redis, waits for the down alert at a stand-in ntfy (`demo/fake_ntfy.py`),
restarts Redis and waits for the recovered alert. To check your own setup, run
`--test-alert` (above), or `python3 omi-host/health/watchdog.py --once`. That prints one
round as JSON and exits 1 if anything is down.
