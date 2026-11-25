---
name: omi
description: "Omi's conversations, day reviews, tasks and calendar."
version: 1.0.0
author: Wizard815, Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [omi, memory, conversations, tasks, calendar, caldav, approvals]
    category: productivity
    related_skills: [weekly-review-planning, meeting-action-items]
---

# Omi Skill

Omi is the user's wearable memory: it records their conversations, writes a summary of each
approved day, keeps their tasks and their CalDAV calendar. This skill uses Omi's MCP server
(`mcp__omi__*` tools) to answer from what the user actually said and to file follow-ups.
It does not approve anything: every task or event you create waits for the user's OK in Omi.

## When to Use

- "What did I promise Gary?", "When is Theo's dentist?", "What did we decide about the car insurance?"
- "How did yesterday go?" / a morning or evening review of the user's day
- Filing a follow-up task or proposing a calendar event the user asked for
- Checking whether a task is done (the user may have ticked it off on their phone)

Not for: the user's own notes or email (other skills), or anything Omi did not hear.

## Prerequisites

- The `omi` MCP server in `~/.hermes/config.yaml` (see `omi-host/examples/hermes/README.md`):
  `url: http://<omi-host>:8000/v1/mcp`, `headers: {Authorization: "Bearer ${OMI_MCP_KEY}"}`.
- `OMI_MCP_KEY` in `~/.hermes/.env`: an Omi MCP key (Omi app, Settings, Developer, MCP, Create key).
- Check: `hermes mcp test omi` lists the tools below.

## How to Run

Load the skill (`/omi`) or let a cron job load it (`skills: [omi]`). Call the tools directly;
no scripts are needed.

## Quick Reference

| Need | Tool | Notes |
|---|---|---|
| Find a conversation | `mcp__omi__search_conversations` `{query}` | Words the user would have said. Returns cards with ids |
| Read one | `mcp__omi__get_conversation_by_id` `{conversation_id}` | Transcript included; `summary_only: true` for the card |
| Browse a day | `mcp__omi__get_conversations` `{start_date, end_date}` | YYYY-MM-DD in the user's zone (`timezone: "profile"`) |
| The day's summary | `mcp__omi__get_day_review` `{date}` | Only approved days have text; a draft day returns `review: null` |
| Tasks | `mcp__omi__get_action_items`, `mcp__omi__search_action_items` | `approval: pending` = waiting for the user |
| New task | `mcp__omi__create_action_item` `{description, due_at}` | Waits for the user's OK, then syncs to CalDAV |
| Done | `mcp__omi__complete_action_item` `{action_item_id}` | `completed: false` reopens |
| Calendar | `mcp__omi__get_calendar_events` `{start_date, end_date}` | status: `on_calendar`, `waiting_for_approval`, `declined` |
| New event | `mcp__omi__create_calendar_event` `{title, start, duration, requested_by: "Hermes"}` | `start` needs an offset; waits for the user's OK |
| Memories | `mcp__omi__search_memories`, `mcp__omi__create_memory` | Facts about the user |

## Procedure

1. **Answer from Omi, cite it.** Search first (`mcp__omi__search_conversations`), open the best
   one or two, and quote what was said with the conversation's date. If nothing matches, say
   so; do not guess. Speaker labels can be wrong: say "in the call with Gary" rather than
   "you said" unless the segment is marked as the user.
2. **Days.** For "how did my day go", use `mcp__omi__get_day_review`. If `review` is null the
   day is not approved yet: tell the user it is waiting in Omi, and do not summarize the raw
   conversations as if they were the approved summary.
3. **Follow-ups.** Before creating a task, `mcp__omi__search_action_items` for the same thing
   (Omi also merges near-duplicates). Create it with a due date when one was said. Then tell
   the user: "Added to Omi; it is waiting for your OK."
4. **Events.** Propose with `mcp__omi__create_calendar_event`, `requested_by: "Hermes"`, an exact
   start with its offset, and a length. Tell the user it waits in Omi's Calendar check.
5. **Completing.** Complete a task only when the user said it is done, or asked you to.

## Approval etiquette

- Nothing you create reaches the user's phone (CalDAV) before they approve it in Omi. Never
  say "added to your calendar" or "added to your list"; say "waiting for your OK in Omi".
- Never re-create a task or event because it is not on the calendar yet: it is waiting.
- A declined task or event was the user's no. Do not propose it again unless asked.
- Omi's day summary is the user's approved account of the day. Prefer it over your own
  reading of the raw conversations.

## Examples

- "What did I promise the plumber?" → `search_conversations {query: "plumber promise"}` →
  "In the call with Gary on Monday: clear everything under the kitchen sink and leave the
  side gate open before Thursday."
- "Remind me to call State Farm tomorrow at 10" → `create_action_item {description: "Call
  State Farm about the multi-car discount", due_at: "2026-11-26T10:00:00-05:00"}` →
  "Added to Omi, waiting for your OK."
- "Put Theo's dentist on Friday at 4" → `create_calendar_event {title: "Theo's dentist", start:
  "2026-11-27T16:00:00-05:00", duration: 45, requested_by: "Hermes"}` → "Proposed in Omi's
  Calendar check; it goes to your calendar once you approve it."

## Morning routine (cron)

```bash
hermes cron create "every 1d at 07:40" \
  "Review yesterday in Omi: read the approved day review (if it is not approved yet, say it is waiting and stop). List promises and follow-ups from it and from yesterday's conversations. For each one not already a task, create it in Omi. Send me a five-line brief: what happened, what is waiting for my OK, what is due today." \
  --skill omi --name "Omi morning review"
```

An evening variant: "every 1d at 21:10", "List today's open Omi tasks due tomorrow and any
event waiting for my OK; ask me nothing, just remind me what to approve."

## Pitfalls

- `get_day_review` without `date` means today in the user's zone, usually not reviewed yet.
- `create_calendar_event` refuses a start without a UTC offset rather than guessing the zone.
- Search is keyword plus vectors; try the words a person would have spoken ("sink", "Gary"),
  not abstractions ("household maintenance").
- A task created here shows `approval: pending` until approved; `get_action_items` lists it.

## Verification

- `hermes mcp test omi` connects and lists `get_day_review`, `create_action_item`,
  `create_calendar_event`.
- After creating a task, Omi's app shows it under "Waiting for your OK".
