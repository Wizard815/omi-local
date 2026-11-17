# Connections, Send to wiki / agent / Coinpile, per-clip keep: evidence

**How these were rendered.** Every image is a production Flutter widget rendered in
`flutter-tester` by the repo's visual-audit harness (`app/scripts/visual_audit.sh --head WORKTREE`),
at 390x844 logical px with a 2x device pixel ratio. The data is synthetic (a loopback fixture
backend). These are **not** screenshots of a running app or device; there is no status bar,
keyboard or safe area, and the harness has no emoji font (a conversation's emoji shows as a box).

The scenarios are in `app/integration_test/visual_audit/scenarios/connections.dart`:
`connections-page`, `send-to-agent-sheet`, `send-to-wiki-sheet`, `send-to-coinpile-sheet`,
`agent-jobs-page`, `approvals-agents-asking` and `day-review-clips-keep-send`. There are no BEFORE
images: these pages and controls do not exist on origin/main.

The behavior behind the screens is checked end to end by `omi-host/demo/integrations_demo.sh`
(35 checks): the real LLMWIKI bridge handler receives the approved day and the chosen
conversation, and nothing unapproved; Hermes, Odysseus, Studio and Coinpile stand-ins record
exactly the previewed text.

| # | What it shows | Notes |
|---|---|---|
| 001 | Settings > Connections: six cards with On / Needs setup / Off and one line each on what is sent | The notice: the developer Conversation Events webhook points at the same wiki, so Omi skips it |
| 002 | The family wiki card: Use this connection, address, token (write-only), Send approved day summaries, Only days I approve myself, Clips I keep (Off / Offer / When I keep) | The token field is never filled from the server; "A token is saved" |
| 003 | Wiki card bottom: Include the transcript, Wiki person, Save, Test connection, Remove saved token, last test result | |
| 004 | Send to agent… opened from a conversation: Odysseus / Hermes / Studio, each with a one-line explanation | Send is disabled until the preview is shown |
| 005 | Prompt typed, "Show what will be sent": the exact text with Omi's context, and "Hermes fits this better" with Use Hermes | Changing the prompt or the agent makes the preview stale and disables Send again (widget-tested) |
| 006 | Send to wiki: the destination file, the exact markdown, "Already sent … Sending again files a revised copy" | |
| 007 | Send to Coinpile: a note drafted from the conversation | "Nothing changes there until you answer Yes on Coinpile's Agent page" |
| 008 | Send to Coinpile: a transaction (account, amount, date) | |
| 009 | Agent jobs: Hermes asks before running a command (Allow once / Deny), Studio working, Odysseus' answer | "What was sent" unfolds the exact text |
| 010 | Waiting for your OK: the agent's question above the tasks | Never part of Approve all, never auto-approved |
| 011 | Approved day: Sent to Family wiki; Orb not sent (auto-approved, and Orb sends only days you approve); a section's clips unfolded, each with a keep star; the section's Send to agent button | Tapping a clip opens its conversation, where title, summary, transcript and action items are editable |
| 012 | The kept clip offers Send to wiki (the wiki is set to Offer) | |

Known limits: strings are English in every locale (the user's choice).
