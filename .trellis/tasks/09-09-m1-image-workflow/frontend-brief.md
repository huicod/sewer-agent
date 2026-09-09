# T05 image execution UI

Implement only in `frontend/src/**` (no dependencies, shared docs, backend or VCS). Existing T04 UI uploads and creates queued tasks, but has stale text claiming execution is not connected.

- Add explicit `启动图片检查` button on queued task cards, calling POST `/api/tasks/{task_id}/start` (empty body), validating the returned TaskRecord, then upserting it. While request pending disable button. Show safe API errors; retry keeps same task ID. Button alone must not create/upload a new task. Video returns safe backend 409; explain video execution is not connected yet without extra metadata requests.
- Continue existing polling and show actual stage, active_tool, retry_count, inspection_result and findings (five labels / OTHER, open name, confirmed/rejected/needs_review states, original-evidence links). Prefer minimal list, no new graph, drawing or report component. Validate all newly consumed nested payload fields before rendering, no raw JSON HTML.
- Text must truthfully distinguish queued upload vs running inspection; T05 does not parse instructions or offer multi-turn dialogue yet (T06), and unresolved findings currently end partial, not an actual human-wait UI (T09). Keep wording concise.
- TDD first new failing test: clicking start calls only fixed endpoint, then server result is displayed; API failure shown without fabricating normal, malformed findings rejected. Update stale T04 expectation to current true text. Preserve File-identity upload retry tests.
- Run `PATH="/root/autodl-tmp/server/.tools/node/bin:$PATH" npm --prefix frontend test -- --run` and `... npm --prefix frontend run build`. No visual browser acceptance claim.
- No subagents, no VCS, use apply_patch for edits. Report `.trellis/tasks/09-09-m1-image-workflow/frontend-report.md` with scope, test command/results, concerns; final short status only. Trellis owns progress; no additional skill ledger/worktree.
