# Edit the day summary text, and Approve all: evidence

**How these were rendered.** Every image is a production Flutter widget rendered in
`flutter-tester` by the repo's visual-audit harness (`app/scripts/visual_audit.sh --head WORKTREE`),
at 390x844 logical px with a 2x device pixel ratio. The data is synthetic: a loopback fixture
backend serves three tasks Omi heard yesterday, two heard dates and yesterday's summary draft (or
an approved day with three sections). These are **not** screenshots of a running app, a device or
Flutter web; there is no status bar, keyboard or safe area.

The scenarios are in `app/integration_test/visual_audit/scenarios/approvals.dart`:
`approve-all-waiting`, `approve-all-tap-undo`, `day-review-approve-all-from-day` and
`day-review-edit-text`. There are no BEFORE images: the scenarios use keys and routes that do not
exist on origin/main (main has no Approve all and no Edit text to compare with).

The behavior behind the screens is checked end to end by `omi-host/demo/caldav_demo.sh` against a
real Radicale: the edited day's all-day VEVENT shows the new text, Approve all puts every waiting
task, event and the summary on the server, Undo takes them off again (the day back to its draft
exactly), and Approve selected with the summary unticked leaves it waiting.

| # | What it shows | Notes |
|---|---|---|
| 001 | Waiting for your OK: Approve all (6), "Hold to choose what to approve", Choose… | Choose… is the accessible alternative to the long-press (also a screen-reader long-press action) |
| 002 | Holding Approve all: the checklist, everything ticked | Tasks, heard dates and day summary drafts |
| 003 | The summary unticked: Approve selected (5) | The user wants to read the summary first |
| 004 | Approve selected done: Undo toast; the summary still waits | |
| 005 | One tap on Approve all: everything approved, Undo toast | No confirmation; Undo reverts exactly the batch |
| 006 | Day review, From this day: Approve all from this day (5) | Scoped to that date; includes that day's draft |
| 007 | Approved day: Edit text | Also in the overflow menu |
| 008 | Edit text sheet: title, each section's heading and text | OmiEditSheet with Save/Cancel |
| 009 | Typed edits | A stray close asks "Discard Changes?" (widget-tested) |
| 010 | Saved: new text, "Edited by you", Undo | Undo sends the previous text back |

Known limits: strings are English in every locale (the user's choice). The Tasks tab uses the same
`WaitingTasksSection` (with Approve all) as 001; it is not shown separately here.
