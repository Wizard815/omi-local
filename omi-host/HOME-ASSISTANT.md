# Home Assistant

Omi and Home Assistant (HA) work together in three ways:

| Direction | What | Default |
|---|---|---|
| Omi → HA | Events (`omi_conversation_finished`, `omi_task_created`, `omi_task_due`, `omi_waiting_for_approval`, `omi_day_summary_ready`) that your automations react to | Off. Each person turns on each event type |
| HA → Omi | The chat agent and MCP agents read the house: `ha_get_states`, `ha_whos_home`, `ha_area_summary` | Only the entities and domains in the read allowlist |
| Omi → HA | The agent asks for an action with `ha_call_service`, for example "turn off the living room lights" | **Waits for your OK** unless you auto-approved that entity. Locks, alarms, covers and garage doors are refused |

The token stays on the Omi server. The app and agents never see it. The API only says whether a token is set.

Code: `backend/utils/home_assistant/`. API: `/v1/home-assistant/*` (`backend/routers/home_assistant.py`). Demo: `demo/ha_demo.sh`.

## 1. Make a token in Home Assistant

1. In HA, open your profile (bottom left), then the **Security** tab.
2. Under **Long-lived access tokens**, choose **Create token** and name it `Omi`. Copy it now: HA shows it only once.

Home Assistant lets only administrators fire events (`POST /api/events`) and render templates (`POST /api/template`). Omi uses templates to look up areas. If you want events and area summaries, the token must belong to an administrator. To keep it apart from your own login, you can create an `omi` user in **Settings → People → Users**, make it an administrator, and create the token as that user. Omi's allowlists below limit what the agent can touch, whatever the token could do.

## 2. Settings in `.env`

```
OMI_HA_URL=http://192.0.2.20:8123
OMI_HA_TOKEN=eyJhbGciOi...
# HA is on your LAN. Omi refuses private addresses unless they are listed here:
OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS=192.0.2.20/32
```

Use your HA's real LAN address; `192.0.2.20` is a placeholder. Then run `docker compose up -d` (compose passes every `OMI_HA_*` variable to `omi-local`).

### Network (egress guard)

- **Egress guard** (`NETWORK.md`): LAN addresses are already allowed. Nothing goes into `OMI_EGRESS_ALLOW`.
- **Host firewall**: containers may reach RFC 1918 ranges (`OMI_EGRESS_LAN`, default `10.0.0.0/8 172.16.0.0/12 192.168.0.0/16`). If your HA is outside those ranges, add its address to `OMI_EGRESS_LAN`.
- **Omi's own rule** (`backend/utils/outbound_http.py`, the same rule as webhooks): a private address must be listed in `OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS`. Loopback and cloud metadata are always refused, so `http://localhost:8123` does not work, even when HA runs on the same host. Use the host's LAN address.
- Each request has a short timeout, because HA is on the LAN: `OMI_HA_TIMEOUT_SECONDS`, default 5 s. Events use 3 s.

### Test it

```bash
# A session token from the app's login, or ADMIN_KEY<uid> on a dev stack
curl -s -X POST -H "Authorization: Bearer $OMI_TOKEN" http://<omi>:8000/v1/home-assistant/test
# {"ok": true, "detail": "API running.", "error": ""}
```

## 3. What the agent may read and do (allowlists)

The operator sets these in `.env`. They apply to everyone, including a person who connected their own HA.

| Variable | Meaning | Default |
|---|---|---|
| `OMI_HA_READ_ALLOW` | Entity ids or domains the agent may read. `*` allows everything | `light,switch,climate,media_player,scene,person` |
| `OMI_HA_ACTION_DOMAINS` | Domains `ha_call_service` may call | `light,switch,scene,script,media_player,climate` |
| `OMI_HA_AUTO_APPROVE` | Entity ids or domains that run without waiting for your OK | none |
| `OMI_HA_EVENTS` | Event types that are on for people who have not chosen | none |

Rules that always apply:

- **Sensitive things**: `lock`, `alarm_control_panel` and `cover`, plus any entity with `garage` in its id (such as `switch.garage_door`), are refused. To allow one kind, name it in `OMI_HA_ACTION_DOMAINS` (`lock`, `cover`, `alarm_control_panel` or `garage`). Even then, **every such call waits for your OK**, and `OMI_HA_AUTO_APPROVE` never applies to it.
- **Never callable**: HA's administration domains (`homeassistant`, `hassio`, `backup`, `update`, `recorder`, `shell_command`, `command_line`, `python_script`, `pyscript`, `rest_command`), even if listed.
- **Safe services only**: `turn_on`, `turn_off` and `toggle`, plus a short list per domain. Climate adds `set_temperature`, `set_hvac_mode`, `set_preset_mode` and `set_fan_mode`. Media players add play, pause, stop, next and previous, volume, `play_media` and `select_source`. Scenes allow only `turn_on`. For the full list, see `SAFE_SERVICES` in `backend/utils/home_assistant/actions.py`.
- **Named entities only**: every target must be an entity of the service's domain, at most 10. `all`, areas, devices and labels are refused. Service data is at most 8 simple fields, such as `{"brightness_pct": 40}`.
- **Approvals expire**: a waiting action can be approved for 1 hour. After that, ask again.
- **Checked twice**: when you approve, the rules are checked again as they are at that moment.

Presence (`ha_whos_home`) reads `person.*`, or `device_tracker.*` if there are no person entities. It works only when `person` or `device_tracker` is in `OMI_HA_READ_ALLOW`.

## 4. Per person

Each person chooses their events and the name the events carry. They can also use their own HA instead of the stack's. The settings are stored in `users/{uid}/integrations/home_assistant`, the same way CalDAV accounts are stored, and the token is encrypted with that person's key.

```bash
curl -s -X PUT -H "Authorization: Bearer $OMI_TOKEN" -H 'Content-Type: application/json' \
  http://<omi>:8000/v1/home-assistant/settings -d '{
    "person": "Alex",
    "events": {"waiting_for_approval": true, "task_due": true, "conversation_finished": true},
    "event_detail": "summary"
  }'
# Their own Home Assistant (optional):  "url": "http://192.0.2.30:8123", "token": "<their token>"
# "token": "" clears their token.  DELETE /v1/home-assistant/settings forgets their choices.
```

If a person sets their own `url`, the stack's token is never sent to that address. They must give their own token too.

The phone app has no screen for this yet. Use the API until it does.

## 5. Events (Omi → HA)

Every event carries `person` (the name above) and `source: "omi"`. Omi never sends a transcript. With `event_detail: "minimal"`, titles, overviews and task text are also left out.

| Event | When | Data |
|---|---|---|
| `omi_conversation_finished` | A conversation has been processed | `conversation_id`, `title` (≤120 chars), `overview` (≤280), `category`, `started_at`, `finished_at`, `task_count` |
| `omi_task_created` | For each task the conversation produced (at most 5) | `conversation_id`, `description` (≤120), `due_at`, `waiting_for_approval` |
| `omi_task_due` | An approved, open task reached its due time (checked every minute) | `task_id`, `description`, `due_at` |
| `omi_waiting_for_approval` | The number of things waiting for your OK went **up** | `count`, `tasks`, `events`, `day_summaries`, `agent_questions`, `home_actions` |
| `omi_day_summary_ready` | The overnight day summary draft is ready to review | `date`, `title` |

Events never hold anything up. If HA is down or slow, the conversation, task or draft carries on. The failure is recorded once (`omi_fallback_total{component="webhook",from_mode="home_assistant"}` plus a warning log line). If HA keeps failing, the webhook circuit breaker skips it for a while.

### Example automations

In HA, go to **Settings → Automations → Create → Edit in YAML**, or put these in `automations.yaml`:

```yaml
- alias: "Omi: flash the hallway light when something waits for my OK"
  triggers:
    - trigger: event
      event_type: omi_waiting_for_approval
      event_data:
        person: Alex
  actions:
    - action: light.turn_on
      target:
        entity_id: light.hallway
      data:
        flash: short
    - action: notify.mobile_app_alex_phone
      data:
        title: Omi
        message: "{{ trigger.event.data.count }} thing(s) wait for your OK"

- alias: "Omi: announce a due task on the kitchen speaker"
  triggers:
    - trigger: event
      event_type: omi_task_due
  conditions:
    - condition: state
      entity_id: person.alex
      state: home
  actions:
    - action: tts.speak
      target:
        entity_id: tts.piper
      data:
        media_player_entity_id: media_player.kitchen_speaker
        message: "Reminder for {{ trigger.event.data.person }}: {{ trigger.event.data.description }}"

- alias: "Omi: the day summary is ready"
  triggers:
    - trigger: event
      event_type: omi_day_summary_ready
  actions:
    - action: notify.mobile_app_alex_phone
      data:
        message: "Your day summary for {{ trigger.event.data.date }} is ready to review in Omi"
```

To see the events arrive, open HA's **Developer tools → Events** and listen to `omi_waiting_for_approval`.

## 6. "Turn off the living room lights" from the pendant

1. You ask Omi: "turn off the living room lights". You can say it to the pendant as a voice question, or use the app's voice chat.
2. The chat agent has the Home Assistant tools because you have a connection. It finds `light.living_room` with `ha_get_states` and calls `ha_call_service(domain="light", service="turn_off", entity_id="light.living_room")`.
3. `light` is allowed but not auto-approved, so Omi stores the call as **waiting for your OK**. Nothing reaches HA yet. Omi answers: "It's waiting for your OK."
4. The waiting count went up, so Omi fires `omi_waiting_for_approval` within about 5 seconds. Your automation flashes the hallway light.
5. You approve it:

   ```bash
   curl -s -H "Authorization: Bearer $OMI_TOKEN" http://<omi>:8000/v1/home-assistant/actions
   # {"actions": [{"id": "d35f…", "domain": "light", "service": "turn_off",
   #               "entity_ids": ["light.living_room"], "status": "waiting_for_approval", ...}], "waiting_count": 1}
   curl -s -X POST -H "Authorization: Bearer $OMI_TOKEN" \
     http://<omi>:8000/v1/home-assistant/actions/d35f…/approve
   # {"ok": true, "action": {"status": "executed", "decided_by": "user",
   #   "result_states": [{"entity_id": "light.living_room", "state": "off"}], ...}}
   ```

   Use `/decline` to say no.
6. Omi calls `POST /api/services/light/turn_off` with `{"entity_id": "light.living_room"}`, and the lights go off.

If you ask for "unlock the front door", Omi refuses at step 3, and nothing reaches HA.

If `OMI_HA_AUTO_APPROVE=light.living_room`, step 5 is skipped and the call runs at once. That never applies to locks, alarms, covers or garage doors.

### The action log

Every call is recorded twice:

- In `users/{uid}/home_assistant_actions`, readable with `GET /v1/home-assistant/actions`. It shows who asked (`requested_by`: `Omi chat`, or the MCP agent's name), the status, who decided (`user`, `auto` or `omi`), errors and the resulting states.
- In the backend log:

```
home_assistant: action uid=… id=… call=light.turn_off light.living_room status=waiting_for_approval by=Pendant
home_assistant: action uid=… id=… call=light.turn_off light.living_room status=executed by=user
home_assistant: action uid=… id=… call=lock.unlock lock.front_door status=refused by=omi detail=lock is not allowed: …
```

## 7. MCP agents (Hermes and others)

An agent connected with an Omi MCP key gets the same four tools. The read tools need the `conversations.read` scope, and `ha_call_service` needs `action_items.write`. MCP calls follow the same rules: they are refused, or they wait for the user's OK. Pass `requested_by` so the log says who asked.

## 8. Try it without a Home Assistant

`demo/ha_demo.sh` starts the real backend against `demo/fake_home_assistant.py`, a small stand-in, not HA. It checks the events, the reads, a light call that waits and then runs after approval, a refused lock call, and that the token never appears in any answer. See `demo/README.md`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `... must be listed in OMI_WEBHOOK_ALLOWED_PRIVATE_NETWORKS` | Add HA's address (or your LAN) to that variable |
| `Home Assistant refused the access token` | Wrong or revoked token, or a person's own URL without their own token |
| Events arrive but the area summary fails | The token's user is not an administrator (HA requires one for templates) |
| `Presence is not shared` | Add `person` to `OMI_HA_READ_ALLOW` |
| The agent has no HA tools | No URL or token for that person, or they set `"enabled": false` |
