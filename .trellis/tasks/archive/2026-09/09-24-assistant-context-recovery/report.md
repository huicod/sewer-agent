# Delivery evidence

Implemented in jj change `kvlkotxo` (parent `041a3c1c`, existing example pack retained):

- Assistant defaults to 420px, pointer/keyboard resize 320–760px constrained by available space, local width persistence and stacked mobile behavior. Removed task-list negative margins that caused horizontal overflow.
- Current viewed task is selected automatically; draft/explicit targets remain pinned while browsing. Context card shows task ID/instruction/mode and allows explicit return to the viewed task. Association survives sends; new upload tasks are followed after linking. Advanced task/reference selection is collapsed.
- Pure-YOLO limits now appear before sending. Explicit same-original recovery creates a new guided baseline and starts it, preserves draft, keeps immutable create/start retry identity and waits while the baseline is active. The old task is unchanged. No automatic geometry submission.
- Updated six example prompts/README, regenerated the ignored 15-file ZIP (950163 bytes), and documented frontend conventions.
- Browser fixture passed the original record's inspection mode to pinned run configuration; its previous default caused configuration_changed for synthetic detector-only starts. Production backend unchanged.

Verification:

- Frontend TypeScript check and 19 Vitest files / 157 tests passed. After final copy adjustment, the affected four component tests passed again.
- Four synthetic browser journeys passed in 45.9s: resizing/persistence/mobile navigation; completed pure-YOLO to guided baseline with exact asset reuse, human review and preserved geometry draft; image/video/report/comparison; lost-acknowledgement retry. Real HTTP/Postgres/Temporal with synthetic model adapters only.
- Inspected desktop and completed-recovery browser screenshots. Browser output: `var/evaluations/assistant-context-recovery/browser-final/`.
- Sequential application stop/build/start passed. Built bundle `index-D12Z3zvW.js`; API, CPU, GPU, PostgreSQL and Temporal ready. Loopback page, JS, CSS and /health returned HTTP 200.
- Evidence logs: `var/evaluations/assistant-context-recovery/{unit.log,recovery-final.log,browser-final.log,build.log}`. The initial failed browser run is retained separately and explained above.

Scoped independent review evaluated context, retries and layout. Source pinning before admission prevents the inserted task from hiding a failed-start recovery button. The comparison control remains on-demand for every completed turn because the planner can infer a comparison without a reference_task_id. Other review concerns were resolved by contract/code inspection: queued turns disable new composer editing, explicit target changes intentionally exit the current recovery wait, and resizing remains available in the two-column tablet layout. Backend validation excludes video detector-only tasks.

Limits: no real example/model inference or geometry tool result was generated during this work; user must inspect actual evidence and make any real human review decisions. The production build still emits the existing non-blocking >500kB chunk-size advisory. No push, migration or public deployment.
