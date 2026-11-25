# Connections: sending to the wiki, Odysseus, Hermes, Studio, Coinpile and Orb

Omi can send a user's things to the family's other self-hosted services. **Nothing leaves
Omi without the user's OK.** Each connection is off until that user turns it on in the
app (Settings → Connections). After that:

| Connection | What leaves | When |
|---|---|---|
| LLMWIKI (family wiki) | The approved day summary, as a source in the user's wiki folder | When the user approves the day (auto-approve counts only if they allow it for the wiki) |
| LLMWIKI | One conversation (edited title, overview, action items, optionally the transcript) | On **Send to wiki** after a preview, or when the user keeps a clip if they chose *Send when I keep a clip* |
| Odysseus | A simple ask (a quick question, a short summary, a reminder, a look-up) with the context the user saw | On **Send to agent → Odysseus** after a preview |
| Hermes Agent | An agentic job (a run); its approval requests come back to Omi's Approvals | On **Send to agent → Hermes** after a preview |
| Ekko / Hermes Studio | A coding-agent job (`/api/studio/orb/work`); Studio's Agent work approvals in those chats are answered from Omi's Approvals | On **Send to agent → Studio** after a preview; an approval only on the person's Approve or Decline |
| Coinpile | A money note or a transaction; Coinpile asks again on its Agent page | On **Send to Coinpile** after a preview |
| Pondering Orb | A short log line "Omi day approved: …" | When a day is approved, if the user turned it on |

herdr is not a target: reach it through Studio's coding agents or a Hermes skill on the herdr host.

## 1. Let Omi reach your LAN

Omi refuses private addresses unless the operator lists them (the same rule as the user's own
webhooks). List each service's address (or the LAN) in `.env`:

```
OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS=<lan-subnet>/24
```

Loopback (`127.0.0.1`), link-local (cloud metadata) and unlisted private addresses are always
refused. A service in another Docker container on the same host is reached by its LAN address
(or list the Docker network, e.g. `172.16.0.0/12`). The egress guard already allows LAN
addresses, so nothing goes into `OMI_EGRESS_ALLOW`.

## 2. Household defaults (optional)

Addresses (and the Hermes, Odysseus and Studio household tokens) can come from the stack; a user's
own address, token and webhook secret in the app win. In `.env`:

```
LLMWIKI_URL=http://<server-ip>:27123        # Obsidian Local REST API port of the vault
HERMES_API_URL=http://<codebox-ip>:8642      # Hermes API server
HERMES_API_KEY=...                           # its API_SERVER_KEY
ODYSSEUS_URL=http://<codebox-ip>:7000
ODYSSEUS_API_TOKEN=ody_...                   # Odysseus Settings → API tokens, scope "chat"
STUDIO_URL=http://<codebox-ip>:8648
STUDIO_API_KEY=sk-studio-omi-...             # Studio: Connections → Omi → Make a key
STUDIO_WEBHOOK_SECRET=...                    # Studio: Connections → Omi → Register the webhook
COINPILE_URL=http://<coinpile-ip>:8080
ORB_URL=http://<orb-ip>:3000
OMI_OUTBOUND_DAY_DELAY_SECONDS=600           # an approved day waits out Approve all's 10-minute Undo
```

## 3. Per person, in the app

Settings → Connections, for each service: the address (or the stack's), the person's token,
**Test connection** (a real call that changes nothing), and the switches.

- **LLMWIKI**: the person's family token (wiki: `node scripts/family-setup.mjs`). The day
  summary is written with `memory_add_source` to `people/<id>/raw/sources/<date> - Omi day review.md`
  (a re-approved day: `Omi day review (revised n)`; sources are never overwritten).
  A conversation goes to `raw/sources/<date> - Conversation - <title> (<id8>).md`.
  If the developer settings' Conversation Events webhook points at the same wiki, Omi skips that
  webhook while Send to wiki is on (otherwise every opened clip would be filed, unapproved).
- **Odysseus**: an `ody_` token with the `chat` scope, and the model endpoint id and model for
  new chats (Odysseus refuses raw endpoint URLs from tokens). Omi creates one Odysseus chat per
  ask (`POST /api/session`, then `POST /api/chat`), or reuses the chat id you set.
- **Hermes**: the API server key. A job is `POST /v1/runs` with `session_id omi-job-<id>` and an
  `Idempotency-Key`; Omi polls `GET /v1/runs/{id}`. When Hermes asks before running a command,
  the question appears in Omi's Approvals and your answer goes to `POST /v1/runs/{id}/approval`
  (`once` or `deny`).
- **Studio**: a Studio key made for Omi (`sk-studio-omi-…`). In Studio: Connections → Omi →
  Omi's address (`http://<omi>:8000`) → **Make a key** (shown once). Paste it as the Studio token
  in the app, or set `STUDIO_API_KEY` for the household; the app's token wins. The key can only
  start and follow Omi's work chats and answer their approvals; anything else answers 403, and a
  revoked key 401 ("not valid any more"). Omi sends it only in the `Authorization: Bearer` header
  and never logs it. An old admin JWT still works, but it can do everything in Studio.
  For results, in Studio: Connections → Omi → **Register the webhook** (it goes to
  `http://<omi>:8000/v1/integrations/studio/events`; the port is the backend's, `BACKEND_PORT`,
  default 8000) and copy the secret it shows once into `STUDIO_WEBHOOK_SECRET` or the person's
  webhook secret in the app. Omi checks `X-Hermes-Signature-256` (`sha256=` + HMAC-SHA256 of
  `<X-Hermes-Timestamp>.<body>`, at most 5 minutes old) and refuses unsigned or badly signed calls
  with 401. Without a secret every call is refused, and Omi polls `GET /api/studio/orb/work` for
  the status instead. **New secret** in Studio means pasting the new one into Omi.
  Studio's Agent work approvals in Omi's chats show in Omi's Approvals (from
  `GET /api/studio/omi/approvals`), each person only those in the chats they started (a household
  key sees every Omi chat in Studio). Approve or Decline posts
  `/api/studio/agent-work/<id>/approve|reject` with the person's first name. The first answer
  anywhere wins: if someone answered in Studio, Mattermost or Orb first, Omi shows Studio's
  "Already approved in Studio by …" and changes nothing. A chat's own "Allow once / Deny"
  question is still answered in Studio.
- **Coinpile**: a `cp_<person>_…` token with the `mcp` scope. Notes use `write_note`,
  transactions `record_transaction`; both only *ask*: nothing changes until Yes on Coinpile's
  Agent page.
- **Pondering Orb**: an `ORB_API_TOKENS` entry (`omi:<token>`). Orb's Journal already includes
  Omi's recap: if you turn on Orb's log line and the wiki send, turn off Orb's own "send this
  day to the wiki" for Omi's part, or the day is filed twice.
