# T06-B1 frontend report

Implemented the durable conversation panel without replacing the existing upload, task-start, polling, evidence, or result UI.

## Behavior

- Added strict `TurnRecord` types/validation and POST/list/detail API clients for the flat conversation contract.
- Restores turns with the persisted session, polls only queued/running turns, clears terminal polling immediately, and ignores stale effect callbacks after cleanup.
- Supports text-only messages, optional CCTV upload, and optional existing-task selection. One active turn disables new intent submission; queued turns can repeat the same POST to confirm startup.
- Keeps request UUID, text, task selection, and uploaded asset identity after ambiguous network failure. A same-intent retry does not upload twice; edited intent creates a new request UUID while reusing an already-uploaded asset when safe.
- Hydrates linked inspections into the existing task list so its existing poller owns ongoing task progress. Conversation completion and linked task running/completed/partial/failed/cancelled states have distinct wording.
- Shows JSON/HTML report links only for validated terminal task IDs, using `/api/tasks/{uuid}/report?format=json|html`. All server text is rendered as React text; no raw HTML or response-provided links are consumed.
- States that supplement actions and video execution are not yet supported.

## TDD evidence

- RED: initial focused run had 9 expected failures because turn API functions and the conversation UI did not exist.
- GREEN: conversation/API/validation focused suite passed 17/17; existing intake/retry compatibility suite passed 7/7.
- Truthfulness RED/GREEN: a linked failed task initially rendered as completed; the focused regression failed, then passed after terminal-status-specific wording.
- Full frontend suite: `npm --prefix frontend test -- --run` passed 25/25 across 5 files.
- Build: the first build found two test-only `ApiError` assertion typing omissions. After correcting them, `npm --prefix frontend run build` passed (`tsc --noEmit` and Vite); the directly affected validation suite then passed 8/8.

## Files

- `frontend/src/App.tsx`, `frontend/src/styles.css`
- `frontend/src/types.ts`, `frontend/src/validators.ts`, `frontend/src/api.ts`
- `frontend/src/conversation.test.tsx`
- `frontend/src/App.test.tsx`, `frontend/src/api.test.ts`, `frontend/src/api-validation.test.ts`, `frontend/src/retry-identity.test.tsx`

No dependencies or VCS state were changed. Backend/Temporal integration is verified separately by the root integration pass.
