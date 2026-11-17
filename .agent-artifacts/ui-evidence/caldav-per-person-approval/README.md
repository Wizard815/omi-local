# Calendar sync per person, and approval before anything is synced: evidence

**How these were rendered.** Every image is a production Flutter widget rendered in
`flutter-tester` by the repo's visual-audit harness (`app/scripts/visual_audit.sh --head WORKTREE`),
at 390x844 logical px with a 2x device pixel ratio. The data is synthetic: a loopback fixture
backend serves Alice's settings on the bundled Radicale account she shares with Bob, three tasks
Omi heard yesterday, two heard dates and yesterday's summary draft. These are **not** screenshots
of a running app, a device or Flutter web; there is no status bar, keyboard or safe area.

The scenarios are in `app/integration_test/visual_audit/scenarios/approvals.dart`:
`caldav-settings-shared-account`, `approvals-waiting`, `day-review-draft`,
`day-review-banner-waiting` and `calendar-check-caldav-only`. There are no BEFORE images: the
scenarios pump pages and keys that do not exist on origin/main (a `--base origin/main` run fails to
compile them), and main had no per-person settings or approval UI to compare with.

The behavior behind the screens is checked end to end by `omi-host/demo/caldav_demo.sh` against a
real Radicale (62 PASS on this branch).

| # | What it shows | Notes |
|---|---|---|
| 001 | Settings, Calendar sync on the shared account: server and account, "shared by everyone on this Omi server", last sync report, My calendar / My task list / Tasks go, Approval with auto-approve off | Defaults for a new user are "Omi – <name>" and "Omi Tasks – <name>" |
| 002 | The task list picker: Bob's lists marked "Used by Bob", Create new… | A calendar is not offered as a task list |
| 003 | Create new…: prefilled with the user's name | Editing sheet (Discard changes? guard) |
| 004 | Tasks go: own list, or under one task named after the user in a shared list | Parent mode makes tasks subtasks (RELATED-TO) of "Alice's tasks" |
| 005 | Parent mode chosen in the shared Family list | |
| 006 | Auto-approve on: its daily time and what it does | Off by default for everyone |
| 007 | Waiting for your OK: three heard tasks (Approve / Edit & approve / Decline), the summary draft, 2 events, auto-approve Off | Opened from the banner |
| 008 | After Approve: the task leaves the list | It now reaches the task list, reminders and CalDAV |
| 009 | After Decline: an Undo toast | Undo makes it wait again |
| 010 | A day summary draft: the notice and Approve the day | The overnight job writes drafts; it never approves |
| 011 | From this day: the day's heard tasks, approvable inline | |
| 012 | The conversations banner names what waits | "3 tasks, 2 events and yesterday's summary waiting for your OK" |
| 013 | Calendar check without dayGLANCE: Add to calendar = my CalDAV calendar; an approved event has Take off | Before this change the page said "no family calendar" and could not approve |

Known limits: strings are English in every locale (as the earlier omi-local strings are); the
banner keeps the old card styling of the day review banner.
