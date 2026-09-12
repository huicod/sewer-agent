# T12-B storage report

## Result

Implemented `InspectionRepository.save_enhancement(task_id, result, call)` with a
task-row lock and one PostgreSQL transaction for derived-asset registration, the
successful `enhance` call, and the fixed `enhance` checkpoint. An existing
checkpoint is the winner: a retry validates the logical task/run/call identity,
returns the stored `EnhancementResult` JSON, and neither registers its new random
output UUID nor changes the winner call.

First writes validate the original task input and dimensions, derived parent and
new output identity, the `enhancement-online-v1` run pin, exact options/model/call
identity, `{run_id}:enhance`, succeeded GPU call shape, floor/ceil ROI provenance,
identity-sized dehaze/deblur output, and x4 super-resolution output. SR pins without
an ROI are rejected. The repository performs no media-file cleanup, so database
rollback and commit-unknown paths leave already-written bytes available for
diagnosis.

## Files

- `backend/src/pipe_inspector/storage/inspection.py`: atomic enhancement save,
  winner replay, and validation.
- `backend/src/pipe_inspector/storage/repository.py`: extracted the existing asset
  insert into a connection-reusing helper without changing public `save_asset`.
- `backend/tests/integration/test_enhancement_storage.py`: 25 real PostgreSQL cases
  using the existing fixture and a unique test-owned schema per case.
- `.trellis/tasks/09-12-m4-enhancement-online/storage-report.md`: this evidence.

## TDD and verification evidence

- RED: `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_enhancement_storage.py`
  failed `1` test with the expected missing `save_enhancement` `AttributeError`.
  A preceding fixture-import collection error was corrected and was not counted as
  RED.
- RED validation batch: the same command produced `7 failed, 4 passed`; six cases
  exposed missing validation and the deliberately malformed-parent fixture initially
  exposed a test-construction error, which was corrected before GREEN.
- RED SR pin: the directed SR-without-ROI case failed because no exception was raised.
- GREEN required suite: `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_enhancement_storage.py`
  -> `25 passed in 9.29s`.
- Focused regression: `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_storage.py backend/tests/integration/test_inspection_storage.py`
  -> `14 passed in 5.25s`.
- Ruff (only modified Python files): `.venv/bin/ruff check backend/src/pipe_inspector/storage/inspection.py backend/src/pipe_inspector/storage/repository.py backend/tests/integration/test_enhancement_storage.py`
  -> `All checks passed!`.

Coverage includes persistent roundtrip, different-random-output replay, two-thread
winner serialization, late conflicting-call rollback, pre-registered output
rejection, fractional ROI rounding, deblur/SR size rules, replay identity, and
task/input/parent/ROI/model/model-version/pin/status/tool/resource/asset-call
mismatches with no partial checkpoint, asset, or successful call.

## Boundaries and risks

- No tables, dependencies, VCS state, shared-service lifecycle, or files outside the
  assigned storage scope were changed.
- Full backend/Temporal/workflow integration was not run; the root agent owns those
  checks. These tests verify the storage contract against the running local
  PostgreSQL service only.
- The method intentionally cannot remove an unregistered media file after a failed
  transaction. Operational cleanup, if ever desired, must distinguish confirmed
  rollback from unknown commit state and is outside this checkpoint.
- Root retains jj integration, independent review, and final acceptance ownership.

## Real Temporal + PostgreSQL recovery integration

Added `backend/tests/integration/test_enhancement_workflow_live.py`, guarded by
`SEWER_RUN_ENHANCEMENT_WORKFLOW_TEST=1`. Each test creates and drops only its own
PostgreSQL schema, uses unique Temporal task queues/workflow IDs, real production
workflow classes, real `ImageActivities`/`SupplementActivities` and
`EnhancementActivities`, the real repository and `MediaStore`, and deterministic
local fakes only at detector/model/reviewer boundaries. It does not call a paid API,
load a GPU model, or start/stop shared services.

The three live scenarios prove:

- A new image executes detector → original-image VLM review → enhancement →
  auxiliary review in order.
- An `enhance_image` activity that commits its checkpoint and then loses its
  acknowledgement survives complete CPU/GPU worker and repository replacement.
  Temporal retries against the stored checkpoint, the lightweight model executes
  exactly once, and the winner asset/checkpoint identity is unchanged.
- An auxiliary conflict enters the real human-review wait, retains the detector's
  original pixel box, and resumes to a human-confirmed terminal result.
- A linked supplement uses its admission-frozen parent task/detection, invokes only
  enhancement and auxiliary review, never schedules detector/original-VLM
  activities, and leaves the parent projection and calls unchanged.

Verification:

- `SEWER_RUN_ENHANCEMENT_WORKFLOW_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_enhancement_workflow_live.py`
  -> `3 passed in 8.26s` (fresh final run; prior green run: `3 passed in 8.24s`).
- `.venv/bin/ruff check backend/tests/integration/test_enhancement_workflow_live.py`
  -> `All checks passed!`.

No production defect was found. Two harness-only setup mismatches were corrected
before the green run: the first fixture revision disabled the repository `.env` and
therefore lacked the configured PostgreSQL password; a subsequent revision treated
`EnhancementActivities` as the detector owner after production had split detector
work into the separately registered `ImageActivities` group. Neither required a
production edit. Completed workflow histories remain under their unique test IDs;
only still-running test-owned workflows are terminated during failure cleanup.
