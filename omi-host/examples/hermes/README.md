# Omi and Hermes

Hermes (hermes-agent) is your main agent; Omi is what you heard and what you approved. They
talk both ways, and your OK is kept on every write:

```
 Hermes ──MCP (Streamable HTTP, Omi MCP key)──▶ Omi /v1/mcp
   reads:  conversations, approved day reviews (never drafts), tasks, calendar, memories
   writes: tasks, calendar events ──▶ Omi "Waiting for your OK" ──(you approve)──▶ CalDAV (phone)
                                                                   ◀──(phone ticks a task off)──

 Omi ──Send to agent (Connections)──▶ Hermes API server POST /v1/runs
   Hermes asks to run a command ──▶ Omi "Waiting for your OK" ──(Allow once / Deny)──▶ Hermes
   the run's answer ──▶ the job, and a note on the conversation you sent it from
```

## 1. Hermes → Omi over MCP

Make a key in Omi (app: Settings, Developer, MCP, Create key; or `POST /v1/mcp/keys
{"name": "Hermes"}` with your session token). Put it in `~/.hermes/.env`:

```bash
OMI_MCP_KEY=omi_mcp_...
```

and the server in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  omi:
    url: "http://omi.lan:8000/v1/mcp"        # your omi-local host; Streamable HTTP (the default transport)
    headers:
      Authorization: "Bearer ${OMI_MCP_KEY}"
    timeout: 120                             # a slow local model can take a while on search
    connect_timeout: 30
    trust: untrusted                         # Hermes asks you before every Omi write (optional:
                                             # Omi already holds the write for your OK)
    tools:
      exclude: [delete_action_item, delete_memory, dismiss_person]
      resources: false
      prompts: false
```

`hermes mcp test omi` should connect and list the tools. Hermes names them
`mcp__omi__<tool>`. The ones this setup is about:

| Tool | What it does |
|---|---|
| `search_conversations`, `get_conversation_by_id`, `get_conversations` | Find and read what was said. Conversations rolled into an approved day are still found. Works without Typesense (Firestore text search on omi-local). |
| `get_day_review` | The approved summary of a day. A day still in draft returns `review: null`: drafts never leave Omi. |
| `get_action_items`, `search_action_items` | Approved tasks. Declined ones never; the ones waiting for you only with `include_waiting: true` (then marked `approval: pending`). |
| `create_action_item` | A task that waits for your OK (no reminder, no CalDAV copy until you approve). `OMI_MCP_TASK_APPROVAL=off` on the Omi host makes MCP tasks approved on creation. |
| `complete_action_item` | Tick off (or reopen) a task; the CalDAV VTODO follows. |
| `get_calendar_events` | Your CalDAV calendar plus the Omi events you approved (`on_calendar`); `include_waiting: true` adds the ones waiting for you and declined ones. |
| `create_calendar_event` | Proposes an event; it waits in Omi's Calendar check and goes to CalDAV once you approve it. It shows in Omi as a small "Hermes asked to add: ..." conversation, left out of the day review. |
| `search_memories`, `get_memories`, `create_memory` | Facts about you. |

Scopes: an Omi MCP key carries all the scopes it can (`conversations.read`,
`action_items.read/write`, `memories.read/write`, ...). The calendar tools ride on
`conversations.read` (read) and `action_items.write` (propose), so no new key is needed.

## 2. The Omi skill for Hermes

`skills/omi/SKILL.md` tells Hermes when to use Omi, which tool for what, and the approval
etiquette ("waiting for your OK in Omi", never "added to your calendar"). Install it either way:

```bash
mkdir -p ~/.hermes/skills/productivity && cp -r omi-host/examples/hermes/skills/omi ~/.hermes/skills/productivity/
```

or point Hermes at this directory:

```yaml
skills:
  external_dirs:
    - /path/to/omi-local/omi-host/examples/hermes/skills
```

A morning review that files follow-ups (it reads only the approved day, and whatever it
creates waits for you):

```bash
hermes cron create "every 1d at 07:40" \
  "Review yesterday in Omi: read the approved day review (if it is not approved yet, say so and stop). List promises and follow-ups; create each one that is not already a task. Send me a five-line brief: what happened, what waits for my OK, what is due today." \
  --skill omi --name "Omi morning review"
```

## 3. Omi → Hermes (Send to agent)

Turn on Hermes' API server (`~/.hermes/.env`):

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=<a long random key>
API_SERVER_HOST=0.0.0.0        # Omi must reach it on your LAN
```

In Omi: Settings, Connections, Hermes: URL `http://<hermes-host>:8642`, token = the
`API_SERVER_KEY`. On the Omi host, list the Hermes host in
`OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS` (Omi refuses private addresses otherwise, and
loopback always). Then "Send to agent" on a conversation, a day section or a task:

1. Omi `POST /v1/runs` with the text you saw in the preview (session `omi-job-<id>`,
   `Idempotency-Key`).
2. When Hermes wants to run something risky, the run waits (`waiting_for_approval`). Omi
   shows the command under Waiting for your OK; Allow once / Deny goes back to
   `POST /v1/runs/{id}/approval` as `{request_id, execution_generation, choice}` (the shape
   Hermes' session-authority runs require).
3. The run's output lands on the job and, with "Add the result to the conversation", as a
   note on the conversation.

## 4. What flows where

| From | To | What | Approval |
|---|---|---|---|
| Omi | Hermes (MCP read) | conversations, approved day reviews, tasks, calendar, memories | drafts and unapproved days are never returned |
| Hermes | Omi (MCP write) | tasks, calendar events, memories | tasks and events wait in Omi; memories go through Omi's memory intake |
| Omi | CalDAV (Radicale, phone) | approved tasks (VTODO), approved events and day summaries (VEVENT) | only after your OK |
| CalDAV | Omi | a task ticked off or edited on the phone; events you add there | your own action on the phone |
| Omi | Hermes (API server) | a job you sent | you pressed Send |
| Hermes | Omi | the run's approval questions and its result | Allow once / Deny in Omi |

## Proof

`omi-host/demo/hermes_demo.sh` runs all of this against a local stack with hermes-agent's
own MCP client and, with `OMI_HERMES_URL`, a real Hermes API server:

```bash
OMI_HERMES_PYTHON=<hermes venv>/bin/python OMI_HERMES_SRC=<hermes-agent checkout> \
OMI_HERMES_URL=http://<lan-ip>:8642 OMI_HERMES_KEY=<API_SERVER_KEY> \
OMI_RADICALE_BIN=<radicale> omi-host/demo/hermes_demo.sh
```

To install hermes-agent for the demo without touching your checkout: copy it, then
`UV_PROJECT_ENVIRONMENT=<venv> uv sync --frozen --extra mcp --extra messaging` in the copy
(`messaging` brings aiohttp, which the API server needs).
