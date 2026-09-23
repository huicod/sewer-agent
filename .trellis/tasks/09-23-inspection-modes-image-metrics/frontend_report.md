# Frontend implementation report

## Implemented

- Added the optional historical wire projection and validated values for `inspection_mode: "yolo_only" | "yolo_vlm"` on task and turn records. New task and turn submissions always send the selected mode; records that omit it render as `yolo_vlm`.
- Added a labelled mode selector to both direct intake and the conversation new-attachment flow. `YOLO＋VLM 复检` is the default and `纯 YOLO（不复检）` is available only for newly selected images.
- Video uploads, linked tasks, and stored video-frame tool operations reset the selection to `yolo_vlm`, disable it, and display the applicable explanation.
- Added mode to immutable pending request identity. An unchanged retry keeps the same `request_id`, asset, and mode. Changing only mode creates a new `request_id` while reusing the identical selected `File` upload.
- Conversation resend from a stored `TurnRecord` preserves its stored mode; historical turns default to `yolo_vlm`.
- Task cards display the saved mode. A completed, covered, inconclusive pure-YOLO run with processed detector output and no findings displays `未检出五类缺陷（未复检）`. Failed/cancelled/incomplete work retains its server result and failure wording.
- Coordinated the image-evidence wire contract with the backend: new evidence payloads contain `inspection_mode` and `review_status` (`completed | skipped_by_mode | unavailable`), while historical payloads may omit both. The panel renders `纯 YOLO，本次未执行 VLM 复检` for the intentional skip instead of presenting it as missing evidence.

## Regression coverage

- Both direct and conversation image submissions send the selected mode.
- Direct and conversation mode changes create a new request identity without a duplicate upload; same-intent retries remain byte-identical.
- Stored queued-turn resend preserves pure mode.
- Image/video switching and stored video-frame selection reset and disable pure mode.
- Saved pure/default historical task modes render correctly.
- Pure zero-detection wording requires successful detector completion and never appears on failed empty tasks.
- Strict validators accept historical and new mode fields, reject unknown modes/statuses and reject contradictory pure-mode evidence status.

## Verification

- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test` — 15 files, 137 tests passed in the latest full run.
- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend run build` — TypeScript check and Vite production build passed.
- `git diff --check -- frontend` — passed before final report; rerun by the integrator after shared-workspace integration.

No backend code, dependencies, Git/jj state, paid APIs, or deployment state were changed by this frontend slice.

## Integration follow-up

- Added backend-compatible `Finding.confirmed_by: "detector_only_rules"` projection. Task cards and stored image provenance identify it as `纯 YOLO 检测规则确认`, retain the original detector score, and do not imply VLM confirmation.
- Added a positive pure-YOLO regression using a backend-shaped confirmed detector finding with original image evidence.
- Tightened new image-evidence field validation: when `inspection_mode` and `review_status` are present, both must be valid string literals and semantically consistent. Only payloads with both fields absent take the historical `yolo_vlm` / `unavailable` defaults; present `null` or numeric values are rejected.
- Latest full verification after this follow-up: 15 test files / 137 tests passed; TypeScript and Vite production build passed.

## Saved-task inheritance follow-up

- Selecting an ordinary persisted task now derives the disabled conversation selector from that task's saved `inspection_mode` (or historical `yolo_vlm` default), and the outgoing turn preserves the same mode. In particular, a saved pure-YOLO image task is no longer silently submitted as `yolo_vlm`.
- Stored video-frame tool selection remains explicitly fixed to `yolo_vlm`; new upload behavior is unchanged.
- Focused verification: `inspection-modes.test.tsx` and `video-frame-tools.test.tsx` passed 27/27 tests; TypeScript and Vite production build passed. The root integrator owns the subsequent final full-suite run.
