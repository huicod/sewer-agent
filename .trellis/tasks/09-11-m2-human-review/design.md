# T09 design

User accepted the current image optimization and requested resumption of the approved
roadmap. This stage is T09, not another model experiment. Existing Finding/TaskSnapshot
contracts carry image/video evidence; the accepted guided experiment is retained as the
image baseline, not silently converted from scored predictions into confirmed findings.

## Durable flow

New runs pin `human_review_version="human-review-v1"`. Old runs without that pin retain
their historical partial/completed behavior. The automated finish activity prepares the
existing `final` checkpoint and publishes `waiting_review` / `human_review` when there
are unresolved findings or an inconclusive result (except fatal detection/config failure
or cancellation). The snapshot has no active tool, progress .95, and can be cancelled.
Workflow then waits without holding an Activity/GPU. It owns review/cancel admission.

An explicit human submission resolves the complete finding set once. The reviewer may
confirm, relabel or reject each item, including an initially confirmed item. No added
boxes, source-image edits, multi-reviewer accounts or repeated review rounds in T09.
Unresolved OTHER can be confirmed as OTHER with a nonempty human name. The optional
negative-audit attestation can complete an audit but cannot invent missing video coverage
or override insufficient quality. No confirmed/unresolved items plus adequate audited
coverage permits normal; otherwise resolved cases can remain inconclusive/partial.

The Temporal update executes `apply_human_review` Activity. One PostgreSQL transaction
locks the task, checks the waiting snapshot identity, writes immutable before/after and
submission audit, and updates the task snapshot. UUID request_id is globally unique;
identical repeats return the original record, changed content conflicts. One record per
task. A lost HTTP/Activity acknowledgement cannot duplicate or undo the review. `final`
retains automated evidence; task snapshot and review record are authoritative after review.
Review/cancel races are serialized in the workflow; stored terminal snapshots never revert.

Only a compact receipt (task_id, request_id, runtime_status, inspection_result) returns
through the Activity/update; HTTP reloads the full audit from PostgreSQL. Submission JSON
is bounded to 1,000,000 bytes using Temporal's default JSON escaping (including Unicode),
with a 2,000,000-byte raw HTTP transport allowance for whitespace/escaping. Other JSON
routes retain their existing smaller limit. The workflow waits for all handlers to finish before returning, following the
[Python SDK guidance](https://github.com/temporalio/sdk-python#signal-and-update-handlers).
The payload bound is a project guard below Temporal's documented payload limits, not a
new external-storage subsystem ([reference](https://docs.temporal.io/cloud/limits#transaction-limits)).

## Shared interface (backend + workflow + UI)

`contracts/human_review.py`:
- `FindingReview`: finding_id UUID; action `confirm|reject|relabel`; label optional
  five-class/OTHER; open_label optional; reason nonempty max1000. Confirm/reject do not
  propose a replacement; relabel requires a label. OTHER name required when confirmed.
- `HumanReviewSubmission`: request_id UUID; reviewer nonempty max100 (UI default huicod,
  self-declared, not an authenticated identity); snapshot_updated_at aware datetime;
  decisions tuple of FindingReview, max2000, IDs unique; negative_audit_complete bool=False;
  note string max2000 default empty. Decisions must exactly cover saved findings.
- `HumanReviewRecord`: task_id UUID; submission HumanReviewSubmission; before TaskSnapshot;
  after TaskSnapshot; reviewed_at aware datetime.
- `HumanReviewDetail`: task_id UUID; can_review bool; snapshot TaskSnapshot;
  record HumanReviewRecord|None.

`storage/human_review.py`: `HumanReviewRepository(VideoRepository)` exposes
`get_human_review(task_id)->HumanReviewRecord|None` and
`apply_human_review(task_id, submission)->HumanReviewRecord`.
Pure `agent/human_review.py` validates submission against snapshot and derives the final
snapshot (preserving finding IDs/evidence, marking confirmed_by=human, removing obsolete
model score when class is changed, preserving original before state in record).
Invalid state/basis/decisions raise `HumanReviewConflict(code,message)`; reused submission
ID with different content raises existing SubmissionConflict.

`workflow/human_review_start.py`: HumanReviewStarter(settings,repo,connect=Client.connect),
`submit(task_id, submission)->HumanReviewRecord`. Read cached record before Temporal,
check equality on repeats; otherwise execute `submit_review` update on `inspection:{id}`.
Validate the compact receipt, then load the exact persisted record for the HTTP response.
HTTP cannot write the snapshot directly. Safe 409 for conflict, 503 for unconfirmed RPC.

API: GET `/api/tasks/{id}/review` -> HumanReviewDetail;
POST same route body HumanReviewSubmission -> HumanReviewRecord (200). GET available for
read-only history; can_review only waiting + new run pin. Old terminal tasks not reopened.
Existing cancel endpoint extended to image/supplement *review waiting*, not active image GPU.

## UI and video

ReviewPanel on waiting tasks displays evidence with original-coordinate boxes, actions,
reason inputs, optional audit attestation, submit/error and read-only accepted record.
No automatic confirmation defaults for pending findings. Retry the exact same submission
with the same request_id; editing content creates a new ID. Task updates refresh server data.
Session reload reopens waiting tasks; accepted audit remains readable.

VideoTimeline reuses GET `/api/tasks/{id}/video`: scan.state.events/active intervals, actual
source seconds/frame indices, decode completeness, and evidence.frames assets. It is a
candidate event timeline, not a new defect-count metric. Use evidence links/source video,
not timestamp-guessing from FPS or a new backend processing pass. No browser playback
guarantee for unsupported codecs; source/frame evidence remains accessible.

## Scope/verification

One task, separate backend / workflow / frontend file owners, then one integrated review.
TDD, scoped tests, frontend build, real existing PG/Temporal with test-owned schemas and
worker replacement while waiting. No paid API/GPU reruns, new dependencies, hashes,
new orchestration framework, model-policy promotion, remote push or historical data edits.
