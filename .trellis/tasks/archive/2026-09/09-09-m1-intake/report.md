# T04 integrated evidence

Date: 2026-09-09. Change: wtkqtltt, parent T03 5d42d0d0. Status: implementation integrated; complete: independent specification and quality review APPROVED, no blocking findings.

## Delivered scope

- Session-linked upload/text submissions, UUID/version original assets, PostgreSQL records and idempotent task creation. No production inspection dispatch or LLM dialogue loop is claimed.
- JPEG/PNG decoded and preserved; MP4/MOV/AVI/MKV metadata and first-frame admission verified with real FFmpeg. Client names/MIME do not select server paths or media types. File/body/pixel/time limits, file-only protocols and safe errors cover admission boundaries.
- HTTP session/asset/task queries survive repository/app reconstruction. Long task projections are persisted queued/pending; no fake processing/normal result.
- React/TypeScript intake UI and shared HTTP contract; main architecture/sole roadmap now explicitly locate session intake in T04 and follow-up dialogue/tool operations in T06.
- Business tables explicitly initialized on the local application database. No Temporal restart, model execution, paid API call, external image upload or remote push in this stage.

## Evidence

- Predecessor gate: archived T03 independent approval plus fresh baseline 145 passed, 3 skipped.
- Root media RED: 11 failures for missing MediaStore. GREEN: 11 passed. Four-container coverage then exposed MOV incorrectly labeled MP4; ffprobe showed shared demuxer name with major_brand=qt. Container-brand fix: 14 media tests passed.
- Root API RED: missing factory injection and unvalidated upload limit (5 failures). GREEN including body/file/form guard extensions: 8 passed. Combined media/API: 22 cases.
- Real HTTP/PG tests: 2 passed, including original download, task replay/conflict, fresh-app session recovery, missing IDs, invalid/valid video. Combined current repository + HTTP integration: 9 passed in 2.97s; only unique test-owned schemas removed.
- Backend suite: 173 passed, 12 skipped in 3.66s; skips include 9 opt-in storage/HTTP tests (separately run) and 3 prior live/restart cases. Ruff passed for backend/runtime/detector/init scripts. Final integrated results are below.
- Local dev smoke: uvicorn /health returned 200; Vite served index.html and proxied a missing-session request to the actual backend's structured 404. This is HTTP connectivity evidence, not visual browser acceptance.
- Storage and frontend detailed results are in their scoped reports. Full review verdict is recorded in review.md, not inferred from test count.

## Remaining boundary / handoff

T05 adds the production image workflow, five-class detector, quality decisions, conditional VLM and negative audit; T06 adds multi-turn answers and linked follow-up tool execution. Existing TaskPlan is for a full inspection (mandatory detect), not a license to rerun detection for each follow-up question. T06 must reuse referenced evidence appropriately.

Real LLM/VLM provider/model/Base URL and local key configuration remain pending; real CCTV video is still needed for effect acceptance. Keys stay only in server configuration. Intake may leave unreferenced original bytes after a process/DB failure; no automatic deletion/garbage collection is introduced. Whole-video integrity, visual browser QA and production supervision are not completed here.

## Final verification and review fixes

- Fresh full backend: 176 passed, 12 skipped in 3.71s; Ruff all checks passed. The 9 storage/HTTP skips were separately executed successfully; live APIs and the prior destructive restart probe were not rerun.
- Fresh frontend: 4 test files / 11 tests passed; tsc --noEmit and Vite production build passed (18 modules).
- Independent backend review found inconsistent string error payloads for invalid Content-Length and malformed/excess multipart. Regression first failed in 4 cases; normalized safe structured errors then yielded 11 passing HTTP guard tests and the full backend result above.
- Root frontend review found same-metadata files could reuse old evidence after a failed submission, and successful JSON lacked runtime checks. File object identity and centralized no-dependency response validators fixed both; worker RED/GREEN evidence is in frontend-report.md.
- Independent frontend review noted React StrictMode may create one unused empty session on a first development-mode load; it does not duplicate uploads/tasks and production build does not double-run that effect. Recorded as a non-blocking development limitation, without expanding T04 into session lifecycle management.
- Native FFmpeg 4.4.2 installed for admission; isolated Node 24.21.0 / npm 11.19.0 installed below .tools/node. Existing system Node and model/CUDA packages were not replaced.
- Temporary uvicorn/Vite connectivity-check processes were stopped after validation; PostgreSQL and Temporal were not stopped. Restart the dev UI using backend/README.md commands.
