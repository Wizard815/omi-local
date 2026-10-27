# CalDAV: tasks and calendar on your own stack

**What.** The backend syncs with a CalDAV server every hour (and on "Sync now" in
the app, Settings → Calendar & tasks):

| Direction | What | Where |
|---|---|---|
| push | every open task and tasks completed in the last 30 days, as VTODOs | the tasks calendar (`Omi Tasks`) |
| pull | a VTODO you tick off on the phone flips the Omi task; VTODOs you create elsewhere become Omi tasks | same |
| push | each day summary as an all-day event, and the events the pipeline extracted from conversations | the events calendar (`Omi`) |
| pull | the calendar's events (last week, next 30 days) so the chat's calendar tool and the app see them | same |

**Where.** `omi-host/docker-compose.yml` runs Radicale as `omi-radicale` and the
backend uses it by default (`CALDAV_DEFAULT_URL`). Set `CALDAV_PASSWORD` in
`.env`; the server refuses to start without it. The same account works from a
phone at `http://<host>:5232/` (DAVx5 for the calendar, Tasks.org or jtx Board
for the VTODO list), so Omi, your phone and a desktop client share one list.

**Why.** Tasks and the day's events are the part of Omi you want outside Omi:
on the phone's calendar, in a tasks app, in Thunderbird. CalDAV is the one
protocol all of those speak, and running the server in the stack keeps the
data local; pointing at another server is one settings screen.

## Bundled Radicale (default)

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

Every user without their own settings then syncs there. The bundled
`omi-radicale` still starts; stop it with `docker compose stop omi-radicale`.

## Checks

```
curl -u omi:$CALDAV_PASSWORD -X PROPFIND -H 'Depth: 1' http://localhost:5232/omi/
```
lists the calendars. In the app, "Sync now" shows pushed/pulled counts and the
last error, if any.

## What is not here

CardDAV (contacts) is not synced. Omi's people (speaker profiles) live in Omi;
exporting them as vCards is a possible follow-up.
