# Omi and FAITH (and ALLGlance, and D.A.S.H)

FAITH is the family hub; Omi is each person's own memory. FAITH talks to Omi's MCP server
directly, per person: in Wizard's view FAITH uses Wizard's Omi key and sees Wizard's
conversations, approved days, tasks and calendar; in the household view (nobody signed in)
Omi is not offered at all. Everything FAITH writes to Omi waits for that person's OK.

## 1. FAITH → Omi, per person

FAITH's generic MCP connector (`server/src/connectors/mcp.ts`) speaks Streamable HTTP with
a bearer token per view, which is exactly what Omi's MCP server takes. No FAITH change.

1. In each person's Omi account, make a key: Omi app, Settings, Developer, MCP, Create key
   (or `POST /v1/mcp/keys {"name": "FAITH"}` with their session).
2. Put the keys in FAITH's `.env`, one per person id from `faith.yaml`:

   ```bash
   OMI_MCP_KEY_WIZARD=omi_mcp_...
   OMI_MCP_KEY_PARTNER=omi_mcp_...
   # no OMI_MCP_KEY_HOUSEHOLD: Omi stays out of the shared view
   ```

3. Add the connector (also in [`omi-connector.yaml`](omi-connector.yaml)) under `connectors:`:

   ```yaml
     - id: omi
       type: mcp
       url: http://192.168.1.5:8000/v1/mcp
       description: "Omi: what this person heard and approved: conversations, approved day summaries, tasks, calendar, memories"
       person_token_env: OMI_MCP_KEY_{ID}
       roles: [admin, adult]
       tools: [search_conversations, get_conversation_by_id, get_conversations, get_day_review,
               get_action_items, search_action_items, create_action_item, complete_action_item,
               get_calendar_events, create_calendar_event, search_memories, get_memories]
   ```

   FAITH names the tools `omi_<tool>`. To let the household view in later (only if you
   want shared Omi reads), add `household_token_env: OMI_MCP_KEY_HOUSEHOLD` with the key of an
   Omi account meant for the whole family; leave it out otherwise.
4. Drop `OMI_OWNER_ID` (docs/setup.md): Omi through ALLGlance went only to one owner; the
   direct connector is per person, so each adult gets their own Omi.

What FAITH sees and does:

| Tool | In FAITH | Approval |
|---|---|---|
| `omi_search_conversations`, `omi_get_conversation_by_id`, `omi_get_conversations` | read (Omi marks them `readOnlyHint`) | none needed |
| `omi_get_day_review` | read; a day still in draft reads as not reviewed | drafts never leave Omi |
| `omi_get_action_items`, `omi_search_action_items` | read; approved tasks only (`include_waiting: true` adds the waiting ones) | declined tasks never |
| `omi_get_calendar_events` | read; what is on the person's calendar (`include_waiting` adds proposals) | |
| `omi_create_action_item`, `omi_create_calendar_event` | write: FAITH's own rules apply first (a child's allowlist, the household's approval queue) | then it waits in that person's Omi "Waiting for your OK" and reaches CalDAV only after they approve |
| `omi_complete_action_item` | write | the person's own task, done |

Proved by `omi-host/demo/peers_demo.sh` (FAITH's own `mcp.ts`, this exact connector entry,
two people, no household key): the household view gets no Omi tools, each person reads only
their own Omi and never a waiting task, and a task or event FAITH creates lands in that
person's Waiting for your OK.

### Mirroring approvals (proposal, not built)

Two queues ask today: FAITH's (`server/src/approvals.ts`, household requests an adult
decides) and Omi's (per person: tasks, events, day summaries, agent questions). A cheap
mirror, in both directions:

- **Omi → FAITH.** Omi already counts what waits (`GET /v1/approvals`: `task_count`,
  `event_count`, `draft_dates`). FAITH could show "3 things wait for your OK in Omi" in the
  person's view by calling a read-only Omi MCP tool `get_waiting_for_approval` (a thin
  wrapper over that summary; not built) on its usual tool path, and link to the Omi app.
  Answers stay in Omi, so nothing is approved twice.
- **FAITH → Omi.** When a household request to an Omi write is approved in FAITH, FAITH runs
  it with the requester's key and Omi still holds it. To avoid asking twice, Omi could
  accept an `approved_by: "faith:<person>"` argument from a key the user marked trusted for
  that, and write the task approved. That is an explicit opt-in per key and needs the
  user's decision; until then both asks are kept.

## 2. Omi → FAITH

Omi's Connections already send work to Hermes, Odysseus and Studio. FAITH's endpoint is
OpenAI-compatible (`/v1/chat/completions`, model `faith`, a per-person `faith token`), so a
FAITH target would be one more chat target: send the previewed text, show the answer on the
job. FAITH's own confirmations (`POST /api/chat/confirm/:id`) would then need to come back
into Omi's Waiting for your OK the way Hermes' approvals do, which is the larger part.
**Follow-up, not built:** it is useful only once FAITH is the family's main front door; for
now Send to agent → Hermes covers agentic work and FAITH reads Omi directly.

## 3. ALLGlance's Omi connector

ALLGlance (`gateway/src/adapters/OmiAdapter.js`) proxies Omi's tools as `omi_*` and adds
Omi's day summary to `allglance_get_day`. Checked against current omi-local by
`peers_demo.sh` with ALLGlance's own adapter:

- auth and transport work as they are (`Authorization: Bearer omi_mcp_...`, Streamable HTTP,
  protocol 2025-03-26);
- a draft day gives no Omi summary (the #17 fix); after approval `daySummary` returns the
  approved title and text;
- `omi_get_action_items` returns approved tasks only (fixed on the Omi side in this change:
  it used to list tasks still waiting for approval, and declined ones);
- `omi_search_conversations` works without Typesense.

For the ALLGlance side (not changed here): on a draft day `daySummary` falls through to
`get_daily_summaries`. On omi-local that is empty (no daily-summary generator runs), so
nothing leaks, but the fallback is only right for Omi servers without `get_day_review`;
ALLGlance could skip it when the key lists `get_day_review`.

## 4. D.A.S.H (the pocket device)

Planning only (no firmware yet). How Omi reaches it, without new Omi code:

- **Through Hermes (Gadget plugin).** The device talks `hermes-gadget.v1` to Hermes; Hermes
  has Omi over MCP and the Omi skill. "What's waiting for me?" → Hermes reads Omi
  (`get_action_items {include_waiting: true}`, `get_calendar_events {include_waiting: true}`)
  and answers on the screen; the morning cron's brief can be pushed to the device. Approving
  stays in the Omi app; the device only shows.
- **Through FAITH.** Signed in as one person (not the household view), the device's FAITH
  chat has that person's `omi_*` tools: today's approved summary (`omi_get_day_review`),
  tasks due, and "add a task" (which waits in Omi). FAITH's `POST /api/chat/confirm/:id`
  covers FAITH's own confirmations, not Omi's.
- **Through Orb.** Orb shows tasks and the day; with Omi's day approvals logged to Orb
  (Omi Connections, "log day approvals"), the device's day view shows that the day was
  approved and its title.

Approving Omi items from the device itself would need an Omi MCP tool that approves (not
offered on purpose: approvals are the user's gesture in the Omi app).
