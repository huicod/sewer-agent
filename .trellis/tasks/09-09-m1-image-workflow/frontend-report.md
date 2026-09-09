# T05 frontend report

## Scope

- Added an explicit queued-task `启动图片检查` action using only `POST /api/tasks/{task_id}/start`; the returned validated task replaces the existing card.
- Added pending-button state, safe API error display/retry, truthful queued/running/partial/video wording, and existing polling remains active.
- Added display of stage, active tool, retry count, inspection result, findings (AJ/BX/FS/PL/BLOCK/OTHER, open label, review state), and original-evidence links.
- Extended the API boundary to validate all newly rendered nested finding, evidence, and coverage fields. No raw JSON rendering, dependency, backend, VCS, graph, drawing, report UI, dialogue, or human-review UI changes.

## Verification

- RED: focused tests failed because the start button/API did not exist, stale queued text remained, and malformed findings were not rejected.
- `PATH="/root/autodl-tmp/server/.tools/node/bin:$PATH" npm --prefix frontend test -- --run`: passed, 4 files / 14 tests.
- `PATH="/root/autodl-tmp/server/.tools/node/bin:$PATH" npm --prefix frontend run build`: passed (`tsc --noEmit` and Vite production build).
- No visual browser acceptance was performed or claimed.

## Concerns

- A queued task record contains only an asset reference, not media kind. To avoid an extra metadata request, the UI explains that only images execute and lets the backend return its safe 409 for video.
- T06 instruction parsing/multi-turn dialogue and T09 durable human review remain intentionally absent. Partial tasks state that unresolved findings do not yet have a human-resolution entry.
## Maintainability follow-up

- Reformatted the new `TaskCard`, `startInspection`, tests, and nested validators into the established multiline style without changing behavior.
- Fresh follow-up verification: 4 test files / 14 tests passed; TypeScript and Vite production build passed.
## Inspection-result validation follow-up

- Added a regression proving an unknown `inspection_result` must be rejected at the API boundary, then restricted validation to `pending`, `normal`, `defects_found`, and `inconclusive`.
