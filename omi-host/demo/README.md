# CalDAV demo: Omi sends and receives tasks and events

`caldav_demo.py` plays the Omi app (the backend's HTTP API) and a phone (DAVx5 +
Tasks.org, as raw CalDAV) against one stack, and checks every change from the other
side: what Omi does must show up on the CalDAV server, and what the phone does must
show up in Omi. It prints a PASS/FAIL table and exits 1 on any FAIL.

| | What it does | Checked with |
|---|---|---|
| send | action items created, edited, completed, deleted through `/v1/action-items` | the VTODOs in `Omi Tasks`: SUMMARY, DUE, STATUS, gone after delete |
| send | a day reviewed (`/v1/users/day-reviews/{date}/finalize` + a paragraph) | the all-day `omi-day-<date>` VEVENT in `Omi` |
| send | the events heard in conversations; one approved with edits, one declined (`/v1/family-calendar/events`, `.../decline`) | `omi-event-<conversation>-<n>` VEVENTs: SUMMARY, DTSTART, DTEND; the declined one gone |
| send | a task created with no "Sync now" | it reaches the server through the scheduler's change pass (~20 s) |
| receive | the phone completes, renames, re-dates (TZID), deletes a task; creates one and completes it | `GET /v1/action-items/{id}`; no duplicate VTODO on the phone |
| receive | the phone creates, moves, renames, deletes an event | `GET /v1/device-calendar/events` |
| receive | the phone moves/renames and deletes an event Omi pushed | the conversation's event (`GET /v1/family-calendar?conversation_id=`): new time, or declined |
| receive | the phone re-opens a task, no "Sync now" | Omi re-opens it at the next full pass (`CALDAV_SYNC_SECONDS`) |
| both | phone and Omi edit the same task before one sync | both end with Omi's title (push runs before pull: Omi wins) |

"Sync now" is `POST /v1/caldav/sync`, the app's button; the demo calls it after each
step so the checks do not depend on timing, and two checks wait for the scheduler
instead.

There is no language model in the demo, so the three family conversations are
seeded with what the extractor would have produced (title, overview, events) by
`seed_day.py`, using the backend's own modules; everything after that goes through
the HTTP API. The family calendar's approve writes to the ALLGlance gateway;
locally `allglance_stub.py` stands in for it.

## Run it here (no Docker)

Needs `backend/.venv` (`make setup-backend`), the Firebase emulators (`npm ci` at the
repo root), `redis-server`, Java 21 and Radicale 3.5:

```
uv venv /tmp/radicale && uv pip install --python /tmp/radicale/bin/python radicale==3.5.4
OMI_RADICALE_BIN=/tmp/radicale/bin/radicale omi-host/demo/caldav_demo.sh          # run, tear down
OMI_RADICALE_BIN=/tmp/radicale/bin/radicale omi-host/demo/caldav_demo.sh --keep   # leave it running
```

The script starts Redis, the Firestore + Auth emulators, Radicale configured by
`omi-host/caldav-env.sh` (as the container is), the stub gateway and the backend with
the container's environment (`OMI_SELF_HOSTED=1`, local login, the in-process
scheduler, `CALDAV_SYNC_SECONDS=60`), all in a scratch directory, then runs the driver.
Ports default to 8000, 8085, 9099, 5232, 6390, 4410 and 8791; `OMI_DEMO_<NAME>_PORT`
moves any of them (see the script's header). It takes about four minutes, most of it
the two scheduler checks.

## Run it against the Docker stack

Run the driver from a machine that reaches the stack (the backend on :8000, Radicale on
:5232). `--isolate` makes a calendar and a task list for the run and points the demo
user at them through `PUT /v1/caldav/settings`, so the family's own `Omi` and
`Omi Tasks` lists are not touched; both are deleted at the end (`--keep-data` keeps
them). Seeding runs inside the container on stdin:

```
CALDAV_PASSWORD="$(docker exec omi-local cat /data/secrets/caldav_password)" \
python3 omi-host/demo/caldav_demo.py --isolate \
  --backend http://<host>:8000 --caldav http://<host>:5232/ --caldav-user omi \
  --seed-cmd "docker exec -i -e OMI_DEMO_SEED omi-local bash -c 'cd /app/backend && PYTHONPATH=. python -'" \
  --full-pass-wait 960
```

- The full pass runs every `CALDAV_SYNC_SECONDS` (900 by default), hence
  `--full-pass-wait 960`; or set `CALDAV_SYNC_SECONDS=60` in `.env` for the run.
- The family-calendar approve is skipped unless you give `--allglance-url`: on a real
  stack it would write to your family calendar. To drive it, run the stub where the
  container can reach it and pass that URL (it only changes the demo user's gateway):
  `python3 omi-host/demo/allglance_stub.py --host 0.0.0.0 --port 8791 --log /tmp/stub.jsonl`
  and `--allglance-url http://<host-lan-ip>:8791 --allglance-log /tmp/stub.jsonl`.
- Each run creates a new login (`caldav-demo-xxxxxx`) with its seeded day; delete it
  in the Firestore UI if you do not want it around.

## A real phone

DAVx5 → add account → "Login with URL and user name":

- URL `http://<host>:5232/` (LAN IP or tunnel; the port is `CALDAV_PORT`)
- user `omi` (`CALDAV_USER`)
- password: `docker exec omi-local cat /data/secrets/caldav_password` (or your
  `CALDAV_PASSWORD`)

Turn on `Omi` (calendar) and `Omi Tasks` (tasks; open them in Tasks.org or jtx Board).
The backend creates both on its first sync. Everyone using the stack shares this one
account and these two lists.

## What does not come back (by design or not yet)

- Day summaries (`omi-day-<date>`) and conversation blocks are Omi's: edits to them on
  the phone are overwritten the next time Omi changes them, and are not read back.
- Clearing a task's due date on the phone leaves Omi's due date (the task helpers
  cannot clear it); any other new due date is taken.
- Phone-side edits of an extracted event change the conversation's event, not an event
  already approved into the family calendar (dayGLANCE).
- Extracted events are pushed once their day is bundled, reviewed or not; the family
  calendar's approve only decides what goes to dayGLANCE. A declined one is taken off.
