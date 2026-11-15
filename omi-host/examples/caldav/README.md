# CalDAV: tasks and calendar on your own stack

**What.** The backend syncs with a CalDAV server by itself: a task or day summary that
changes reaches the server within about 20 seconds, every member is fully synced every
15 minutes (`CALDAV_SYNC_SECONDS`), and "Sync now" in the app (Settings → Calendar &
tasks) runs it at once:

| Direction | What | Where |
|---|---|---|
| push | every open task and tasks completed in the last 30 days, as VTODOs | the tasks calendar (`Omi Tasks`) |
| pull | a VTODO you tick off on the phone flips the Omi task; VTODOs you create elsewhere become Omi tasks | same |
| push | each day summary as an all-day event, and the events the pipeline extracted from conversations | the events calendar (`Omi`) |
| pull | the calendar's events (last week, next 30 days) so the chat's calendar tool and the app see them | same |

**Where.** The `omi-local` container runs Radicale itself (`omi-host/caldav-env.sh`,
started by the entrypoint next to the backend; there is no separate container) and the
backend uses it by default. Its calendars live on the data volume
(`/data/radicale/collections`), so the volume backups cover them. The same account
works from a phone at `http://<host>:5232/` (DAVx5 for the calendar, Tasks.org or jtx
Board for the VTODO list), so Omi, your phone and a desktop client share one list.

**Why.** Tasks and the day's events are the part of Omi you want outside Omi:
on the phone's calendar, in a tasks app, in Thunderbird. CalDAV is the one
protocol all of those speak, and running the server in the stack keeps the
data local; pointing at another server is one settings screen.

## Bundled Radicale (default)

Nothing to set. On first start a password is generated and kept on the volume:

```
docker exec omi-local cat /data/secrets/caldav_password
```

Or choose your own:

```
# .env
CALDAV_PASSWORD=choose-something-long
# optional
CALDAV_USER=omi
CALDAV_PORT=5232
```

Phone: DAVx5 → add account → "Login with URL and user name" →
`http://<lan-ip>:5232/` + the user and password. It discovers `Omi` and
`Omi Tasks` (the backend creates both on first sync). Tasks.org (with DAVx5)
shows `Omi Tasks`.

**Moving from the old `omi-radicale` container.** Compose mounts its volume
read-only at `/radicale-legacy`, and the first start copies its calendars into
`/data/radicale` (never over existing ones). Same port, user and password, so
phones keep working. Once copied, remove the `radicale-data:/radicale-legacy:ro`
line and the `radicale-data` volume from `docker-compose.yml`, and
`docker compose up -d --remove-orphans` drops the old container.

## Your own server, per user (app settings)

In the app: Settings → Calendar & tasks → server URL, user, password → "Test
connection" lists the calendars; leave the calendar fields empty and the
backend picks the first event calendar and makes an `Omi Tasks` one, or paste a
calendar URL from the test result.

| Server | URL to enter | Notes |
|---|---|---|
| Nextcloud | `https://cloud.example.org/remote.php/dav/` | use an app password (Settings → Security) |
| Baïkal | `https://dav.example.org/dav.php/` | |
| Radicale elsewhere | `https://host:5232/` | |
| iCloud | `https://caldav.icloud.com/` | app-specific password; iCloud has no VTODO calendars by default: make a Reminders list first |
| Google Calendar | not CalDAV-friendly for tasks | use the Google integration upstream ships, or DAVx5 on the phone |

## Whole-stack default to another server

```
# .env
CALDAV_DEFAULT_URL=https://cloud.example.org/remote.php/dav/
CALDAV_USER=alice
CALDAV_PASSWORD=app-password
```

Every user without their own settings then syncs there. The bundled server
still runs (phones may use it); `OMI_BUNDLED_CALDAV=0` turns it off.

## Checks

```
curl -u omi:$CALDAV_PASSWORD -X PROPFIND -H 'Depth: 1' http://localhost:5232/omi/
```
lists the calendars. In the app, "Sync now" shows pushed/pulled counts and the
last error, if any.

## What is not here

CardDAV (contacts) is not synced. Omi's people (speaker profiles) live in Omi;
exporting them as vCards is a possible follow-up.
