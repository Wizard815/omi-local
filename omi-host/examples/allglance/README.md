# ALLGlance: Omi and the family calendar on one stack

**What.** [ALLGlance](https://github.com/Wizard815/ALLGlance) is one gateway (MCP + REST
+ CalDAV) over Vikunja and the GLANCE apps: the household's tasks, day plan, chores and
milestones. Omi connects to it three ways, and each one is a setting on one side:

| Direction | What moves | Where it is set |
|---|---|---|
| Omi → Vikunja, CalDAV | every open action item as a VTODO, both ways: tick it in Vikunja and Omi completes it; add one in Vikunja and Omi gets it | `CALDAV_DEFAULT_URL=http://<host>:8090/dav/` with `CALDAV_USER` / `CALDAV_PASSWORD` set to a Vikunja login (or a Vikunja CalDAV token) |
| Omi → gateway, MCP + REST | the chat assistant gets `allglance_get_day`, `allglance_list_tasks`, `allglance_list_chores`, `allglance_list_milestones`, `allglance_create_task`, …; a new Omi action item is created in Vikunja at once | `ALLGLANCE_URL=http://<host>:8090`, `ALLGLANCE_TOKEN=<the gateway's GATEWAY_TOKEN>` |
| gateway → Omi, MCP | Claude (or anything on the gateway) gets `omi_get_day_review`, `omi_get_conversations`, `omi_search_memories`, …, and `allglance_get_day` carries Omi's day summary | in ALLGlance's `.env.allglance`: `OMI_URL=http://<host>:8000`, `OMI_MCP_TOKEN=<an Omi MCP key>` |
| Omi → dayGLANCE, **after asking** | a date Omi hears ("the inspection is Friday at eleven") goes on the family calendar as a dayGLANCE block, but only once you have checked it in the app: *Settings → Family calendar* (or the "events to check" card on the conversation list) shows each heard event with its title, note, date, time and length to fix, add or decline; *Adding as* picks which household member (from the gateway's roster) the events belong to | the same `ALLGLANCE_URL` / `ALLGLANCE_TOKEN`; the gateway needs dayGLANCE on (`ENABLE_DAYGLANCE=true`, `DAYGLANCE_MCP_HOST/PORT/TOKEN` in `.env.allglance`) |

**Where.**

- `backend/utils/allglance.py`: once an hour (the same local scheduler as the day bundle
  and CalDAV), for every active member, seeds a private "ALLGlance" chat app from the
  gateway's `/mcp` (tools discovered once, cached ten minutes) and an `allglance` task
  integration, and makes that integration the default when the member has none. A
  member who saved their own ALLGlance integration from the app is left alone.
- `backend/utils/task_integrations_ops.py`: the `allglance` provider, `POST
  /api/allglance/tasks` on the gateway with its bearer token (`external_task_id` is
  `vikunja:<id>`).
- `backend/routers/apps.py`: `POST /v1/apps/mcp` takes an optional `access_token` for
  any bearer-protected MCP server; that is what the seed uses.
- `backend/utils/caldav/sync.py`: the sync looks for an events calendar only when an
  event direction is on, and when the server has none (Vikunja: task lists only) it syncs
  the tasks and says so in the sync report instead of failing.
- `backend/utils/family_calendar.py` + `routers/family_calendar.py`: `GET /v1/family-calendar`
  lists the events heard in the last week that nobody has reviewed (and the ones reviewed
  lately); `POST /v1/family-calendar/events` writes **one** approved event, with the user's
  corrections, as `POST /api/allglance/tasks` `{source: "dayglance", start: "YYYY-MM-DD HH:MM",
  duration_minutes}` in the member's time zone, and stores `calendar: "allglance"` and
  `external_id: "dayglance:<id>"` on the conversation's event; `POST
  /v1/family-calendar/events/decline` stores `declined`; `PUT /v1/family-calendar/owner`
  binds Omi to one household member (the task integration's `owner_id`, sent as the
  gateway's `owner_id` = dayGLANCE's `assignee_id`, and used for Vikunja tasks too). That is the only writer: no
  scheduler, no hook in conversation processing, so an event never appears on the family
  calendar unseen. App: `app/lib/pages/family_calendar/`. The self-hosting dashboard
  (`backend/routers/dashboard.py`) probes the gateway's `/health` and the CalDAV URL as
  components and shows an ALLGlance & Calendar card with each member's binding, waiting events
  and last CalDAV sync.
- `backend/utils/mcp_server/handlers/other.py`: the `get_day_review` MCP tool (also
  `GET /v1/mcp/day-reviews/{date}`), what the gateway's `omi_get_day_review` and
  `allglance_get_day` read.

**Why.** Tasks belong where the family looks for them, and the day's words belong where
the tasks are: the gateway already fronts both calendars and agents, so Omi plugs into
it instead of growing a second task list. CalDAV carries the tasks because it is the one
protocol every task app speaks; MCP carries the rest because the gateway and Omi both
already serve it. Events ask first because the extractor is wrong about dates often enough
(a weekday said on a Sunday, "at three" that was 3 pm, a guessed length) that an unreviewed
family calendar would stop being trusted; one look per event costs seconds.

## Set-up (ten minutes)

On the ALLGlance host (`.env.allglance`):

```
GATEWAY_TOKEN=<long random>         # what Omi sends as ALLGLANCE_TOKEN
OMI_URL=http://192.168.20.5:8000    # omi-host's backend
OMI_MCP_TOKEN=omi_mcp_...           # Omi app -> Settings -> Developer -> MCP -> Create Key
```

On the Omi host (`omi-host/.env`):

```
ALLGLANCE_URL=http://<allglance-host>:8090
ALLGLANCE_TOKEN=<the same GATEWAY_TOKEN>
CALDAV_DEFAULT_URL=http://<allglance-host>:8090/dav/
CALDAV_USER=<vikunja username>
CALDAV_PASSWORD=<vikunja password or CalDAV token>
```

Then `docker compose up -d` on both. Within the hour (or at once with
`POST /v1/caldav/sync` from the app's Calendar & tasks page) tasks are in Vikunja, the
chat answers "what's on today" from the gateway, and the gateway answers "how did my
day go" from Omi.

## What is not synced, and why

- Heard events go to dayGLANCE only through the review above, never on their own; events
  (and day summaries) do not go to Vikunja over CalDAV: Vikunja has task lists only. They reach ALLGlance through MCP (`omi_get_day_review`). To have them
  on a calendar too, keep Omi's bundled Radicale as the CalDAV server instead; Omi talks
  to one CalDAV server per member.
- Omi's action items are not a task source inside the gateway: they are already in
  Vikunja through CalDAV or the task integration, so listing them again would show every
  task twice.
- Omi's chat tools from the gateway use the gateway's one token, so they see the whole
  household (the gateway's `owner_id` filter narrows a question to one person).
