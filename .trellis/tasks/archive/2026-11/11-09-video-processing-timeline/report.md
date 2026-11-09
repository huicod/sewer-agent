# Video processing timeline delivery — 2026-11-09

## Delivered
- Always-visible steel-blue processing axis beneath the source player: exact persisted scan span, packed candidate intervals, bounded evidence markers and all-frame selectors.
- Playback/time slider/candidate/evidence seeking remains read-only and never autoplays. Selected persisted frame previews on demand; a separate explicit action admits it to the existing conversation selection only.
- Additive API reads committed in-flight state and exact frame-zero PTS, with final checkpoint priority, source/version validation and no writes or schema migration.
- Preserve native PTS. Unknown start or unmapped nonzero/out-of-duration timestamps disable linked seeking while retaining original player and exact-frame browsing. A finalized head alone never establishes decode completion.
- Fix task/source request races, reuse metadata across tasks with the same video, and clear playback on media error. All tracks share one coordinate width.

## Verification
- Frontend: 30 files / 261 tests pass, including 10 axis, 8 playback integration and 27 progress boundary tests. TypeScript and production build pass.
- Backend: 13 inspection API tests pass; scoped Ruff passes. No model execution required.
- Independent review findings fixed: state/head count consistency, post-metadata media failure, and coordinate alignment.
- Real browser at 1600x1000 and 390x844: 9-second source; candidate seek 0.48s, saved frame 8 seek 0.32s, keyboard seek 8.99s, active playback tracked at 1.32s. Exact saved frame image loads. Explicit selected frame appears in conversation; no API writes. HTML/JSON report entry points remain operational. No page errors or horizontal overflow. All three tracks x=348 / width=771 in desktop check.
- All 9 existing video detail endpoints read successfully. One historical source starts at PTS 0.0333666667; linked seeking is conservatively disabled because an original-PTS/player mapping is not established, while evidence browsing and native playback remain available.
- Exact before/after equality for 8 sessions, 25 tasks and 1 turn, not only counts. No inspection, human decision, model call, database migration or record rewrite performed.

## Local deployment
- http://127.0.0.1:18082/ updated to sewer-agent:video-timeline-20261109 (manifest 0f7566fee5e49855f9bf1a4c73444762eacd1f5e0fae4e44f18778cc3523e2f9).
- API, CPU/GPU workers, Postgres and Temporal healthy. Runtime imports/readability checked before activation.
- WSL source sync checked 10 owned files against 27659246 baseline; unrelated local changes preserved.
- Backup: /home/lnsdf/sewer-agent-backups/video-timeline-20261109 (three database dumps, assets/settings, protected environment, prior source copies). Rollback image: sewer-agent:before-video-timeline-20261109.
- Local evidence remains ignored under var/timeline-*.{log,json,png,mjs,py,sh}; generated build, media, settings and secrets are not included in source history.
- Existing non-blocking build bundle-size and test-environment pseudo-style warnings unchanged.
