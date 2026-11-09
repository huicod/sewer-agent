# Delivery evidence — 2026-11-09

## Result
- Implemented the explicitly selected 02 engineering workbench, 05 formal HTML report and 04 structured JSON viewer.
- Original image/video is the primary work surface; persisted findings with direct evidence previews and continuing conversation occupy the supporting pane. Video timeline controls sit next to the player. Original file access, task history, review controls, settings and mode/provenance remain available.
- Task/session report entry points now open a read-only viewer. Authoritative HTML includes metadata, source/result tables and real evidence images, with print/download access. JSON has a tree, selected-field/record tables, full raw data, copy/download and separate missing/null values.
- No database schema, detector, model configuration, stored result or credentials were changed.

## Verification
- Frontend full regression: 27 files / 215 tests passed. After the final missing/null regression was added, the report suite passed all 10 tests; final media/selected-frame regression passed 28 tests. TypeScript and production Vite build passed.
- Backend initial report regression: 53 tests passed. Final original-video-reference regression added two tests; the affected 22-test subset and Ruff passed. All 8 real session reports / 25 sources passed schema validation and authoritative HTML rendering.
- Independent scoped reviews covered controller/draft preservation, media failure retry, responsive width/focus, report escaping/CSP, late responses, absent/null handling and original-video versus saved-frame preview provenance. Actionable findings were fixed.
- Actual browser on local data: original image loaded, video played beyond 1 second with readyState 4, no page errors. Opening JSON reports preserved an unsent draft. JSON source table showed the 7 actual input groups in the selected example.
- Formal session HTML: 30 tables, 7 real images, 0 broken images. Report directory navigation scrolled within the frame; print control invoked the frame print function. A real single-task A4 PDF was generated under print CSS.
- Desktop 1600px, intermediate 1100px, mobile 390px and standalone report 320px checked. No page/report horizontal overflow; keyboard resize changed actual width and aria-valuenow together (420 → 436).
- Before/after API snapshots matched exactly: 8 sessions, 25 tasks, 1 conversation turn. Inspection, asset and persisted snapshot identities are unchanged. Browser acceptance blocked API writes and made no model or human-review submissions.

## Local deployment
- Serving at `http://127.0.0.1:18082/`; API, CPU/GPU workers, database and Temporal are healthy.
- Runtime image `sewer-agent:engineering-ui-20261109`, also tagged `sewer-agent:local`. Production assets: `index-hO3KHA5E.js`, `index-BCja7JZE.css`.
- Reused the verified runtime dependency image and replaced source/frontend assets; the repository Dockerfile remains the clean-build path. No model weights or secrets entered this commit.
- Backup: `/home/lnsdf/sewer-agent-backups/engineering-ui-20261109-150250`, containing three database dumps, asset/settings archive, protected environment copy and replaced-source backups. Rollback image `sewer-agent:before-engineering-ui-20261109` remains available.
- First local package had root-only intermediate directories from archive extraction; services were restored before correcting permissions. Runtime-user import/readability checks passed before reactivation. No data loss or migration occurred.
- WSL deployment source updates were limited to 23 UI/report files after verifying their pre-change bytes against the prior release; final report compatibility fix was then synchronized separately. Unrelated existing deployment edits were preserved.

## Boundaries
- Visual/behavior/media/report validation does not claim new model inference accuracy. Existing review states and findings are preserved.
- HTML downloads reference the current deployment's evidence URLs; they are not self-contained offline media bundles. Original complete JSON remains available.
- Local screenshots, test logs, acceptance PDF and exact API snapshots are in ignored `var/ui-*` artifacts, not public source history.
