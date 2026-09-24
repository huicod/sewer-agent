# Independent conversations delivery

2026-09-24. Controlling workflow: Trellis; jj change `yqvryysp`, parent `31b7b332` (`kvlkotxo`). User authorized independent Ant Design conversations, collapsible controls, continuing image tools and unified reports. Default is to start a conversation for an image and continue there; multiple explicit uploads in one conversation are supported. Existing sessions are not split or merged automatically.

## Delivered behavior

- Server-backed conversation list/search/create/switch replaces task navigation. Central messages own the available space; context and attachment/mode/target controls collapse; the task/evidence panel is optional, resizable on desktop and an overlay on mobile. Same-asset task history remains available within the conversation.
- Session-keyed in-memory drafts retain text, File references, mode, target and immutable retry identity while switching. Late acknowledgements cannot clear newer pending requests or edited unsent drafts. Reload restores the latest linked task context; newly admitted tool results become the follow-up target while ordinary questions preserve detail navigation.
- Original-image tool requests can continue from an existing child, resolving the exact same-session/same-asset root and freezing its evidence. New request IDs create separate operation children; identical request IDs still deduplicate. Per-run tool constraints and pure-YOLO mode restrictions remain. Video-frame operation quotas remain separate.
- `GET /api/sessions`, complete saved conversation history, and JSON/HTML `/api/sessions/{id}/report` are available. Unified reports retain source task/root/role, snapshots, dialogue, calls, detector/VLM provenance, enhancement and geometry evidence. Sources are not summed into a physical defect count; active work is non-final.
- Six existing manual sample instructions and their README now match the independent UI. The local ignored `example/manual-test-pack.zip` was regenerated from the 15 source files; sample images and prompt bodies are unchanged.

## Verification

- Backend full suite: **1222 passed, 165 skipped**, Ruff passed. Log: `var/evaluations/independent-backend-final.log`. Skipped tests include opt-in service/model evaluations, not silent passes.
- Frontend full suite: **23 files / 174 passed**. Subsequent late-ack draft case and final guards: **3 relevant files / 13 passed**, including one additional regression. TypeScript and managed production build passed. Logs: `independent-unit-final.log`, `independent-final-draft-guards.log`, `independent-app-build.log` under `var/evaluations/`.
- Explicit isolated PostgreSQL session listing and repeatable-image admission/migration/idempotence checks: **26 passed** (`independent-storage-final.log`). Earlier agent integration checks additionally covered surrounding enhancement/geometry storage.
- Explicit synthetic Temporal image-supplement integration: **3 passed**, exercised continuation from both original and child, same root, distinct requests and persisted evidence. Used synthetic provider responses, not live inference.
- Synthetic browser acceptance: **4 passed**, final run `var/evaluations/independent-conversations/browser-run3`; real browser/HTTP/PG/Temporal with synthetic media/model adapters. Includes desktop resize, mobile navigation, conversation drafts, YOLO recovery, image/video/frame flow, comparison, unified report and lost acknowledgement. Earlier failing adaptation evidence remains in `browser-run1`; successful prior run in `browser-run2`.
- Independent review found late-response retry clearing, stale reload context and missing child-follow behavior; all addressed with regression coverage. An adjacent unsent-draft race was reproduced red then fixed. Final cache-only guard refinement ran the 13 relevant tests and production typecheck/build after browser acceptance; no layout or HTTP contract changed afterward.
- Complete change scope/diff inspected; `git diff --check` passed. No mutable Git operations, pushes or release were performed.

## Local runtime

Stopped managed API/CPU/GPU gracefully, explicitly ran `.venv/bin/python scripts/init_storage.py`, built with `.venv/bin/python scripts/app.py build`, then started with `.venv/bin/python scripts/app.py start`. API, CPU, GPU, PostgreSQL and Temporal all report ready at `http://127.0.0.1:8000`. Nine read-only HTTP checks returned 200 (page, health, both static assets, sessions, session detail/history and both unified report formats); six saved conversations remain available. Evidence: `var/evaluations/independent-runtime-health.json`.

Initialization adds `sewer_image_operations` and copies historical one-slot lineage idempotently. It neither deletes historical tables nor rewrites original task records. The prior binary cannot express newly admitted multiple children: code rollback alone is not a safe admission rollback; retain forward schema/data and stop writes before planning any rollback. The parent jj change remains the code review anchor.

## Boundaries for manual acceptance

No live model/provider/GPU inference was submitted, and no artificial human decisions were applied to real CCTV. Real example geometry quality and output remain for the user's manual test; synthetic acceptance demonstrates workflow behavior only. Grouping uses stored AssetRef identity, not image-content deduplication; continuing the same image requires staying in the conversation and omitting a new attachment. Drafts survive conversation switches in the current page, not a browser reload. Saved server history survives reload. Existing model context remains bounded; complete UI/report history does not imply unlimited model context. Production build reports the existing large-bundle warning (about 639 kB minified JS), without a build failure.
