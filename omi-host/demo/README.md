# CalDAV demo: Omi sends and receives tasks and events

`caldav_demo.py` plays the Omi app (the backend's HTTP API) and a phone (DAVx5 +
Tasks.org, as raw CalDAV) against one stack, and checks every change from the other
side: what Omi does must show up on the CalDAV server, and what the phone does must
show up in Omi. It prints a PASS/FAIL table and exits 1 on any FAIL.

| | What it does | Checked with |
|---|---|---|
| approve | heard tasks, heard events and the day's summary draft before any approval | none of them on the server; `GET /v1/approvals` counts them; tasks carry `approval=pending` |
| approve | tasks approved (one edited) or declined (undone once), the day approved (`/day-reviews/{date}/approve`), reopened and approved again | the approved ones on the server, the declined one not, the summary off while reopened |
| send | action items edited, completed, deleted through `/v1/action-items` | the VTODOs in the user's own `Omi Tasks – <name>`: SUMMARY, DUE, STATUS, gone after delete |
| send | the approved day | the all-day `omi-day-<owner>-<date>` VEVENT in the user's own `Omi – <name>` |
| send | the events heard in conversations, approved in the Calendar check; one approved with edits, one declined (`/v1/family-calendar/events`, `.../decline`) | `omi-event-<owner>-<conversation>-<n>` VEVENTs: SUMMARY, DTSTART, DTEND; the waiting and declined ones absent |
| people | a second Omi user on the same server and CalDAV account, same date | their own calendar and task list; both day summaries on the server, neither overwritten; approve without a gateway; the collections API names who uses what; "Create new…" moves the tasks without deleting any |
| auto | auto-approve off (default), then on at 00:00, run as the hourly job runs it | the waiting task stays off, then is approved and synced |
| legacy | two people synced first with the pre-change sync code (`--legacy-ref`, default the parent of the commit that introduced per-person UIDs), both into the shared `Omi`/`Omi Tasks`; then the new code; then one of them makes lists of her own (Create new…) | they stay on the shared lists with their old UIDs, nothing duplicated or deleted; the shared `omi-day-<date>` becomes one summary each; her task, event and summary move (not copy) to her new lists; nothing of the other person's is touched |
| edit | the approved day's title and text edited by hand (`PUT /day-reviews/{date}/text`) | still approved; the all-day VEVENT shows the new SUMMARY and DESCRIPTION; a rewrite answers 409 until confirmed |
| all | a third person (no family-calendar gateway): Approve all of her day (`POST /v1/approvals/batch`), its Undo (`/batch/undo`), then Approve selected with the summary unticked | her task, event and summary on the server, then all three off again with the day back to its draft unchanged; then task and event on, the summary still waiting; a second Approve all approves only the summary |
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

Last run here (this branch): 85 passed, 0 failed, 0 gaps, 2 info.

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

Every Omi user on the stack shares this one account, but each gets a calendar and a
task list of their own, `Omi – <name>` and `Omi Tasks – <name>`, made on their first sync
(pick others, or make new ones, in the app: Settings, Calendar sync). Turn on yours (open
the task list in Tasks.org or jtx Board). Someone who synced before per-person lists
existed keeps the shared `Omi` and `Omi Tasks`. Nothing Omi heard reaches these lists
until its owner approves it.

## What does not come back (by design or not yet)

- Day summaries (`omi-day-<date>`) and conversation blocks are Omi's: edits to them on
  the phone are overwritten the next time Omi changes them, and are not read back.
- Clearing a task's due date on the phone leaves Omi's due date (the task helpers
  cannot clear it); any other new due date is taken.
- Phone-side edits of an extracted event change the conversation's event, not an event
  already approved into the family calendar (dayGLANCE).
- Extracted events are pushed once approved in the Calendar check (with or without
  dayGLANCE); a declined one is taken off. Events pushed before approvals existed are
  kept and marked approved.

# Connections demo: what Omi sends to the wiki, Odysseus, Hermes, Studio, Coinpile and Orb

`integrations_demo.sh` starts the same kind of stack as `caldav_demo.sh` (Redis, the
Firestore + Auth emulators, the backend with the container's environment, the in-process
scheduler, local login) on its own ports, plus:

* the **real LLMWIKI MCP bridge handler** (`wiki_bridge_server.cjs`: the plugin's own
  `serveFamilyMcp` from `Wizard815/LLM-will-remember-this`, over the in-memory two-person vault
  its tests use, `scripts/family-harness.cjs`), read back through a `/__vault` window;
* recording stand-ins for Hermes, Odysseus, Studio, Coinpile and Orb (`agents_stub.py`), each
  answering the way that service's code does (paths in its header). The Studio stand-in signs
  and posts its `chat.run.completed` webhook back to Omi like Studio's dispatcher.

They listen on this machine's LAN address, never loopback (Omi refuses loopback targets), which
the script allows with `OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS=<ip>/32` as an operator would; the
backend runs under omi-host's egress guard in block mode. The outbound queue works every 3 s and
an approved day waits 6 s instead of Approve all's 10-minute Undo window, so a run takes about
two minutes.

```
LLMWIKI_REPO=/path/to/LLM-will-remember-this omi-host/demo/integrations_demo.sh
```

Ports default to 8100, 8185, 9199, 6490, 4510 (stack) and 27223, 8742, 7100, 8748, 8180, 3100
(wiki, Hermes, Odysseus, Studio, Coinpile, Orb); `OMI_DEMO_<NAME>_PORT` moves any of them and
`OMI_DEMO_LAN_IP` picks the address.

| | What it does | Checked at |
|---|---|---|
| settings | every connection set through `PUT /v1/integrations/outbound`, then Test connection | tokens never echoed (`has_token`); each probe reaches its service |
| gate | the night job's summary **draft**, then Approve all + Undo inside the window | nothing in the wiki vault, no Orb log line |
| wiki | the day approved; reopened and approved again | `people/wizard/raw/sources/<date> - Omi day review.md`, then `... (revised 1).md`; the receipt on the day review |
| wiki | Send to wiki on one conversation, again, and a revised copy | the vault file equals the preview; "already sent"; `(revised 1)` |
| hermes | Send to agent: a job | `POST /v1/runs` with exactly the preview, its question in `GET /v1/approvals`, Allow once relayed with the `request_id`, the output stored |
| odysseus | Send to agent: a simple ask | `POST /api/session` (endpoint, model), `POST /api/chat`, the answer stored |
| studio | Send to agent: a coding job | `POST /api/studio/orb/work`; the signed webhook completes the job; an unsigned one gets 401 |
| coinpile | Send to Coinpile: a note and a transaction | `write_note`, `record_transaction` with exactly the fields; Coinpile's "asks first" answer |
| egress | the backend's interpreter | the guard is on (block), a public address is refused, no LAN call was |

Last run here (this branch): 35 passed, 0 failed.

# Hermes demo: Hermes uses Omi over MCP, Omi hands work to Hermes

`hermes_demo.sh` starts the CalDAV demo's stack and runs `hermes_demo.py` with
hermes-agent's own MCP client (`hermes_mcp_probe.py` registers `mcp_servers.omi` and calls
the tools through Hermes' dispatcher) and, with `OMI_HERMES_URL`, a real Hermes API server
for Send to agent. Setup and what flows where: `omi-host/examples/hermes/README.md`.

```
OMI_HERMES_PYTHON=<hermes venv>/bin/python OMI_HERMES_SRC=<hermes-agent copy> \
OMI_HERMES_URL=http://<lan-ip>:8642 OMI_HERMES_KEY=<API_SERVER_KEY> \
OMI_RADICALE_BIN=/tmp/radicale/bin/radicale omi-host/demo/hermes_demo.sh
```

Rows: Hermes connects with an Omi MCP key; reads a draft day as not reviewed and the
approved one with its summary; finds a conversation rolled into the approved day; its tasks
and events wait for the user's OK (and stay out of agents' reads), then reach Radicale once
approved; a phone completion comes back; Hermes completes a task; a real Hermes run's
approval is answered in Omi and its result lands on the conversation.

# Peers demo: ALLGlance and FAITH read Omi

`peers_demo.sh` runs ALLGlance's Omi connector and FAITH's generic MCP connector (with
`omi-host/examples/faith/omi-connector.yaml`, one Omi key per person, no household key)
against the same stack: no drafts and no waiting tasks reach either, FAITH's household view
has no Omi tools, and what FAITH writes waits in that person's Waiting for your OK.

```
OMI_ALLGLANCE_REPO=<ALLGlance checkout> OMI_FAITH_SERVER=<F.A.I.T.H/server copy, npm installed> \
OMI_RADICALE_BIN=/tmp/radicale/bin/radicale omi-host/demo/peers_demo.sh
```

## Backup demo: back up, lose the disk, restore, same counts

`backup_demo.sh` runs the CalDAV demo above on a throwaway stack, backs it up while it runs
(`backup/omi_backup.py`, quiesced with `maintenance.sh`'s own export and Redis snapshot),
deletes the stack's data, restores onto an empty directory, starts the stack from it and
compares what the user and the stores show (`backup_demo.py`). Then it runs the test
restore (`backup-verify`) and the health watchdog against the restored stack, kills Redis,
and waits for the down and recovered alerts at a stand-in ntfy (`fake_ntfy.py`). Same
prerequisites as the CalDAV demo; its ports default away from it.

```
OMI_RADICALE_BIN=/tmp/radicale/bin/radicale omi-host/demo/backup_demo.sh          # run, tear down
OMI_RADICALE_BIN=/tmp/radicale/bin/radicale omi-host/demo/backup_demo.sh --keep   # leave stack, watchdog, fake ntfy
```

See [BACKUP.md](../BACKUP.md) and [HEALTH.md](../HEALTH.md).

## Proxy demo: the stack behind NGINX, as through a tunnel

`proxy_demo.sh` runs the CalDAV demo's stack behind two real nginx running
`omi-host/proxy/nginx/omi.conf`: one as shipped, one with a 30 s idle limit standing in for
Cloudflare's ~100 s cut. The model is `slow_llm_stub.py`, silent for 75 s before it answers.
`proxy_demo.py` checks login, unbuffered chat streaming, the slow answer surviving the idle limit
on keep-alives (and the same silent model being cut without them), the `/v4/listen` upgrade,
CalDAV methods at `/caldav/` and on a `caldav.` hostname, and a 150 MB upload. Needs nginx
(`apt-get install nginx-light`; 1.27.3+ also checks the shipped files with `nginx -t`).

```
OMI_RADICALE_BIN=/tmp/radicale/bin/radicale omi-host/demo/proxy_demo.sh
OMI_DEMO_IDLE_SECONDS=100 OMI_DEMO_LLM_DELAY=130 OMI_RADICALE_BIN=... omi-host/demo/proxy_demo.sh   # Cloudflare's numbers
```

See [REMOTE-ACCESS.md](../REMOTE-ACCESS.md); `omi-host/proxy/check_remote.sh` checks the real hostname.
