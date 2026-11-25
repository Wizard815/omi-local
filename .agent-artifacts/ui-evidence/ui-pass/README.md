# Self-hosted app UI pass: evidence

**How these were rendered.** Every image is two production Flutter widget renders side by side,
BEFORE (origin/main `0c9c1bddd5`) on the left and AFTER (this branch) on the right. They come from
the repo's visual-audit harness (`app/integration_test/visual_audit/`), run in `flutter-tester` at
390x844 logical px and a 2x device pixel ratio. The data is synthetic (the loopback fixture backend).
Both sides used this branch's harness and scenarios. These are **not** screenshots of a running app
or device: there is no status bar, keyboard or safe area. The harness has no emoji font, so a
conversation's emoji shows as a box.

The edge states use the harness's new knobs: `OMI_AUDIT_TEXT_SCALE=2.0` (accessibility text),
`OMI_AUDIT_WIDTH=320` (a small phone) and `OMI_AUDIT_BRIGHTNESS=light`. `OMI_AUDIT_REPORT_LAYOUT=1`
prints each overflow with its widget and keeps capturing; with it, the AFTER side reports no layout
errors in any of these states. The new scenarios are in
`app/integration_test/visual_audit/scenarios/ui_pass.dart`.

The survey covered 32 scenarios (Home and the conversation list, conversation detail and its menu,
the Send sheets, Day review (draft, sort, sections and clips, Edit Text, receipts), Waiting for Your
OK with Approve all and the checklist, agent questions, Tasks, the calendar check, CalDAV settings,
Connections, Agent Jobs and chat). Each was captured at 1x, 2x text, 320 pt and in the light theme.
Only the states that changed, plus one for reference, are kept here.

| # | State | What was wrong | Now |
|---|---|---|---|
| 001 | Chat, 3 min into a reply on a slow model | Each 20 s server keep-alive was added as its own "Still working…" step line, and the shimmer stops after 5 s, so the reply looked stuck | One step line. After 20 s, one quiet "Still working on it. This can take a few minutes." line, which is a live region for screen readers. The client also no longer times out before the server does (see the PR) |
| 002 | Same at 2x text | Same | Same |
| 003 | A conversation stored at once by the self-hosted server, with its notes still being written (Summary tab) | "Processing conversation…" with no hint that the transcript is already there | "Writing notes… The transcript is ready to read now." The page keeps checking for 30 min (it used to stop after 3 min) |
| 004 | Same, Transcript tab | The strip said "Processing conversation…" while Home said "Writing notes…" | "Writing notes…", the same words as Home |
| 005 | Day review before bundling, light theme | **Bundle the day** and **Keep 1 · Summarize 11** were black text on a black button | OmiButtons |
| 006 | Day review banner on Home, light theme | A fixed dark card with white text, and the close button had no label | Theme tokens, and the close button is labelled "Dismiss" |
| 007 | Day reviews list, the read fails | It looked like a normal list (or "No reviewed days yet") | OmiErrorState with Try Again. It also has a loading state, an empty state and a back button |
| 008 | Day reviews list, light theme | White text on a white page | OmiSettingsGroup rows |
| 009 | Waiting for Your OK | The summary row title was a lowercase "yesterday's summary", and the buttons were in sentence case | "Day summary · Tue, Nov 24", and Title Case: Edit & Approve, Approve All (6) |
| 010 | Approve all checklist at 320 pt | "Can…" / "Approve selecte…" were cut off | OmiButtonPair stacks the two, with the primary on top |
| 011 | Agent Jobs at 2x text | The header row overflowed by 8.6 px, and "Allow …" was cut off | The header wraps. Allow Once / Deny are stacked |
| 012 | Agent Jobs at 320 pt | "Allow on…" was cut off | Stacked |
| 013 | CalDAV settings at 320 pt | "Sync n…" / "Test conn…" were cut off | Stacked |
| 014 | Calendar check at 2x text | "Add events as" broke inside words ("even / ts as"), and Not This / Add to Calendar overflowed by 92 px | At large text, OmiSettingsRow puts the value under the title. The buttons wrap |
| 015 | Transcript at 2x text | The transcript lines stayed at 1x (`RichText` does not scale) and did not use the theme font | The lines scale |
| 016 | Conversation menu | "Send to wiki", "Send to agent…" sat next to "Copy Transcript" in the same menu | Send to Wiki, Send to Agent… |
| 017 | Day review sections and clips | Times ignored the 24-hour setting and locale, and were shown in UTC | OmiDateFormat on local time ("7:48 – 8:32 AM") |
| 018 | Home, a conversation being processed | — | Unchanged; kept for reference. It shows "Processing… / Writing notes… · 1 recording" and not the stored title (see Known limits) |

Changes that have no image here: the same date fix in receipts, Agent Jobs, Send to Wiki, CalDAV's
last sync and the checklist. Day review's own load failure now shows localized text with Try Again,
not the server's words, and Copy Summary goes through OmiClipboard. Connections' load failure uses
OmiErrorState. Pushed self-hosted pages have OmiBackButton. Send to Wiki's button is full width.
"Nothing is waiting" is hidden while an agent is asking.

Known limits:
- New strings are English in every locale, as in earlier PRs.
- At 320 pt the CalDAV page title ellipsizes ("Calendar & Tasks (Cal…") next to the back button.
- The Send to Coinpile amount label is shortened at 2x text.
- The Home processing row says "Processing…" rather than the title the server already stored. That
  is the row design from earlier PRs and was left as it is.
