# B2 linked re-review storage report

## Result

Implemented the bounded server-owned parent link and atomic PostgreSQL supplement admission. Writes stayed within the storage brief's allowlist.

## RED / GREEN evidence

- Contract RED: `backend/tests/test_supplement_contracts.py` failed 2 tests because `parent_task_id` was forbidden. GREEN: 2 passed.
- Storage RED: the new integration suite first failed collection because `storage.supplements` did not exist. During implementation it caught a psycopg connection/cursor API error and a same-request concurrency race that returned `supplement_limit` instead of the first child.
- Final GREEN: `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_supplement_storage.py` — 23 passed in 11.46s.
- Final contract check: `.venv/bin/pytest -q backend/tests/test_supplement_contracts.py` — 2 passed in 0.13s.
- Scoped lint: `.venv/bin/ruff check backend/src/pipe_inspector/contracts/intake.py backend/src/pipe_inspector/storage/inspection.py backend/src/pipe_inspector/storage/supplements.py backend/tests/test_supplement_contracts.py backend/tests/integration/test_supplement_storage.py` — all checks passed.

## Exact interface

- `TaskRecord.parent_task_id: UUID | None = None`; legacy payloads remain valid and a task cannot parent itself.
- `InspectionRepository.update_snapshot(snapshot: TaskSnapshot) -> TaskRecord` preserves every current record field, including `parent_task_id`, while replacing and validating the snapshot.
- `SupplementNotAllowed(code: str)` is a safe `RuntimeError` with public `.code`.
- `SupplementRepository(ConversationRepository)`:
  - `initialize() -> None` additively creates `sewer_image_supplements(parent_task_id PRIMARY KEY, task_id UNIQUE)` with both task foreign keys.
  - `admit_supplement(parent_task_id: UUID, submission: TaskSubmission, config: dict) -> TaskRecord` atomically creates the linked queued child, immutable run pins, copied detect checkpoint, and frozen `source` checkpoint.

Admission replays a globally identical request for the same parent before validating new caller config, including after acquiring the parent lock for concurrent retries. Different requests serialize on the parent and only one consumes the permanent logical slot. The child receives no copied verify/final checkpoint and no tool-call record.

## Coverage and concerns

The isolated-schema PostgreSQL tests cover reopen/replay and first pins, conflicting identity, simultaneous same/different requests, nested and failed-child limits, completed/partial eligibility, non-ready/failed/cancelled parents, video/derived images, session/asset mismatch, missing/invalid/unpinned evidence, invalid child config, source snapshot equality, snapshot-link preservation, and transaction rollback after a late serialization failure. Each fixture drops only its random test schema.

`SupplementRepository.initialize()` must run before admission so the additive relation table exists. Full-suite, Temporal, GPU, and live-provider checks were intentionally not run because the storage brief excludes them; root owns caller/workflow integration.
