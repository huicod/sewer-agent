# Backend unit requirements

Read design.md in this directory; its Shared interface is binding. User resumed approved
T09 human review; implement only backend contracts/policy/storage/HTTP boundary.

Owned files: new backend/src/pipe_inspector/contracts/human_review.py,
agent/human_review.py, storage/human_review.py, api/human_review.py,
workflow/human_review_start.py; existing api/app.py and scripts/init_storage.py.
Tests: new backend/tests/test_human_review.py, test_human_review_api.py,
integration/test_human_review_storage.py. Do not change workflow/start.py, workflow run
classes/activities, worker script, frontend, unrelated tests, history or dependencies.

Repository subclasses VideoRepository; use additive idempotent table initialization,
short transaction + task FOR UPDATE lock. Audit table one row per task and unique request_id;
store full before, after, submission, reviewed_at. Validate UUID conflict even across tasks.
`get_human_review(task_id)` returns record or None, and missing task must be rejected at API.
`apply_human_review(task_id, submission)` returns identical record on identical retry, even
after task terminal. Changed request content conflicts, including same task new request ID
after one completed batch. Check exact snapshot_updated_at and exact finding IDs. After
review all findings are human-confirmed or rejected; same label/source evidence preserved;
relabel changes source=human and score=None. No source evidence edits.

Pure `resolve_human_review(snapshot, submission, *, now)` -> TaskSnapshot. Reject non-waiting
snapshot; never report normal without the coverage/quality/audit contract. Human attestation
only adds to negative_audit_complete (existing audit OR new attestation); preserve sufficient_coverage/quality. Completed with
defects_found if any confirmed and coverage/quality adequate, or completed normal if all
rejected and audited; otherwise partial/inconclusive as appropriate. Preserve existing
failure/limitations in waiting_reason alongside review note, do not erase failed ToolCalls.
ReviewStatus rejects have confirmed_by=None; confirmations canonical_label required, OTHER
requires human-provided open_label (confirm may supply name but not replacement label).

Starter: actual Temporal update name `submit_review`, payload submission.model_dump(json);
task identity from handle `inspection:{task_id}`. The update returns only a compact receipt
{task_id,request_id,runtime_status,inspection_result}; validate it, load the persisted audit,
compare submission identity/content and return HumanReviewRecord via HTTP. Bound compact
submission JSON to 1,000,000 bytes using Temporal's default Unicode escaping (safe schema
validation error when exceeded); review HTTP raw-body allowance is 2,000,000 bytes.
The root gate
may fail with ApplicationError types review_not_waiting/review_in_progress/review_conflict;
translate safe domain 409. Connection/timeouts translate InspectionStartError 503 without
exception body. Existing stored identical record must return before connecting Temporal.
Before making update check waiting + supported run pin; reject legacy/terminal otherwise.

Router GET/POST /api/tasks/{task_id}/review as design. API app defaults to HumanReviewRepository,
accepts optional human_review_starter injectable; registration shouldn't call DB or models.
initialize script uses new repository. Keep tests with injected existing fake repositories
working unless the new endpoint is called. Root owns worker and cancellation extension.

TDD (RED then GREEN), isolated PostgreSQL integration opt-in SEWER_RUN_STORAGE_TEST=1.
No live service starts yourself; root will arrange them. Do not print DSN/settings/secrets;
short tracebacks for integration. No nested agents or VCS mutation. Write concise results
and RED/GREEN test evidence to backend-report.md here; tell root interfaces are ready early.
