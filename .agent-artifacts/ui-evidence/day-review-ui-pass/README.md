# Day review UI pass: evidence

**How these were rendered.** Every image is a production Flutter widget rendered in
`flutter-tester` by the repo's visual-audit harness (`app/scripts/visual_audit.sh`), at 390x844
logical px with a 2x device pixel ratio. The data is synthetic: a loopback fixture backend serves a
made-up family day (eight clips, a summary in four sections, five tasks, three heard dates). These
are **not** screenshots of a running app, a device or Flutter web. The run used
`--base origin/main --head HEAD`, so "BEFORE" images are origin/main (0c57f4ad1a) and the rest are
this branch. Emoji show as boxes because the test fonts have no emoji glyphs. That is a limit of
the harness, not an app bug. There is no status bar, keyboard or safe area.

The scenarios are `day-review-family-sort`, `day-review-family-summary`,
`family-calendar-family-day` and `conversations-family-day-organize` in
`app/integration_test/visual_audit/scenarios/`. `tasks-edit`,
`family-calendar-in-conversation` and `conversations-grouped-row` were already there.

## Images

| # | What it shows | Finding |
|---|---|---|
| 001 | The day before review: eight held clips, Grandma starred, "Bundle the day" on top, sort-by-hand below | Readable. The three trailing controls take about 40% of each row, so titles wrap early (low) |
| 002 | Two swim clips ticked: the merge bar appears | Before this branch the tick had no label (the `dayReviewMergeTooltip` string existed but was unused). It is labelled now |
| 003 | The merged group, with Ungroup | OK |
| 004 | Bedtime dropped: Keep 1 · Merge 1 · Drop 1 · Summarize 4 | Ticked-but-not-merged clips are counted as "Summarize" (low) |
| 005 | **BEFORE**: tapping "Bundle the day" after starring, merging and dropping | **High.** It POSTs `/bundle` at once, with no confirmation. The server bundle reads only stars that are already saved, so the page's choices are thrown away and freshly starred conversations are rolled out of the list. (The fixture had no `/bundle` route, hence the error toast) |
| 006 | AFTER: Bundle asks "Build the day summary?" and sends the page's keep/merge/drop | Fixed. The copy "1 will be merged into one conversation each and 1 dropped" is awkward (low, not changed) |
| 007 | Bundled day: title, counts, kept conversation, timed sections | Good |
| 008 | A pending correction highlighted inline, an added paragraph under its section, "Apply corrections (2)" | The added paragraph also appears again in "Your corrections" further down (low) |
| 009/010 | AFTER: "+" opens an editing sheet, and closing it with text typed asks "Discard Changes?" | **Medium, fixed.** Before, a bare AlertDialog lost the typed text on a tap outside |
| 011 | Saved: "Apply corrections (3)" | Edits are notes until Apply runs an LLM rewrite. You cannot edit the text directly or edit a pending correction, only delete and re-add it (medium, design) |
| 012 | AFTER: removing a correction shows an Undo toast | **Medium, fixed.** Before, it was removed with no confirmation and no Undo (INV-UI-3) |
| 013 | AFTER: "From this day" links to Tasks and the Calendar check | **Medium, added.** Before, the day review ended at the summary with no route to the day's tasks or heard dates |
| 014 | Tasks made that day (each row has a chevron to the task editor; not tapped in this run) | Rows show no due date or source conversation (low). Mobile tasks from conversations are written directly; the approve step exists only as Suggestions on the Tasks tab |
| 015 | Task edit sheet | OK |
| 016-018 | Calendar check: three dates, the title fixed (Edited badge), Add (moves to Reviewed lately) | Good ask-first flow |
| 019 | **BEFORE**: after "Not this" | **Medium.** The decline was final, with no Undo and no way back |
| 020 | AFTER: Undo toast, and Bring Back on the declined row | Fixed (backend `declined: false` plus app) |
| 021 | A conversation's summary with a Calendar row | OK |
| 022 | Conversation row menu: Star, Move to Folder, Select (merge) | There is **no way to split** one conversation that covered two topics (the pediatrician call also covered Leo's soccer form). Not contained, not built |
| 023 | Two conversations selected for merge | The merge action bar lives in the home shell, outside this pumped page, so it is not shown |
| 024 | A capture group (several devices recording the same event) offers Recordings and Separate | "Separate" splits devices, not topics |
