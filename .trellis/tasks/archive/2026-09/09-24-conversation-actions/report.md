# Conversation actions delivery

2026-09-24. Trellis-controlled bounded change; jj change `zqxoqylw`, parent `fc8a046b` (`yqvryysp`).

Each conversation now has an Ant Design more menu with rename, pin/unpin and archive/restore. The sidebar separates unarchived and archived conversations, keeps search and sorts pinned items first. Creating from the archived/search view returns to the unfiltered conversation list. Archiving the active conversation preserves its keyed controller and draft, with a restore banner below the header. Pending changes are acknowledged by the server before the list changes; failed renames keep their modal/input. Metadata is preserved when task polling refreshes inferred summaries. Escape dismisses menus/dialogs without collapsing the desktop navigation or the mobile drawer underneath.

The server persists custom_title/pinned/archived/metadata_updated_at through additive session columns. PATCH /api/sessions/{id} updates only supplied fields and returns the complete summary; null title restores inferred naming, invalid titles/booleans and empty/unknown patches are rejected. Archive neither deletes saved content nor changes task execution. Existing task, turn and report routes remain available.

## Evidence

- Frontend full suite: 24 files, **187 passed**. Subsequent Escape regression verification: **19 passed** across the sidebar/shell files, including two added desktop/mobile cases. TypeScript and managed production build passed. Logs under `var/evaluations/conversation-actions/`: frontend.log, menu-escape-green.log, build.log.
- Root backend intake/API/report/control tests: **35 passed**, Ruff passed. Backend agent contract/intake tests: **18 passed**; isolated PostgreSQL/API tests: **6 passed**, including schema upgrade, partial update preservation, retained task/turn history, pinned ordering and validation/404 behavior.
- Synthetic browser run1 exposed global Escape folding the navigation; fixed with a regression. Run2 verified the fix and passed the other **4 scenarios**; the new case then failed only because the test clicked AntD's visually hidden radio input. Switching to the visible category label was verified in run3: new management case **1 passed**. No product changes after run2. Screenshots in browser-run3 show more actions and archived conversation with retained draft; root visually inspected both menu and archive layouts.
- Independent review found no blocking defects. Optional frontend metadata fields intentionally remain compatible with historical fixtures; current server responses supply all three fields and validate supplied values.
- Scoped diff reviewed and whitespace checks passed. A concurrently added unrelated `docs/EXPERIMENT_RESULTS_SUMMARY.md` was left untouched and is outside this task's delivery.

## Local service update

Gracefully stopped the managed app, explicitly ran `.venv/bin/python scripts/init_storage.py`, built with `scripts/app.py build`, and started with `scripts/app.py start`. API/CPU/GPU, PostgreSQL and Temporal report ready on 127.0.0.1:8000. Eight read-only page/static/API checks returned HTTP 200; all six pre-update conversation IDs remain available and metadata fields validate. No real conversation was renamed, pinned or archived by validation. Runtime evidence: `var/evaluations/conversation-actions/runtime-health.json`.

Migration is additive/idempotent and can briefly lock the sessions table. The prior code remains a review/rollback anchor; reverting this feature's code leaves its metadata columns/data intact but older UI hides those controls. Do not drop columns to roll back. Tests use isolated/synthetic records, with no live model inference submitted for this feature. Existing non-failing JS bundle size warning remains. No remote pushes or mutable Git operations were performed.
