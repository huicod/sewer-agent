# T05 inspection storage report

## Result

- Added `InspectionRepository(PostgresRepository)` with idempotent DDL for prefixed run, tool-call, and checkpoint tables.
- Run records pin first-written config and return JSON-boundary string UUIDs. Same config reuses the run; changed config conflicts.
- Tool calls are keyed by logical `call_id` plus `attempt`. Updating one attempt preserves all other attempt records and rejects cross-task/logical-key reassignment.
- `SUCCEEDED` and intentional `SKIPPED` calls can commit atomically with a first-write checkpoint. Replays return the stored payload rather than overwriting it.
- Snapshot updates lock the task row, preserve request/session/input/instruction/creation fields, and make a terminal projection immutable against later regressions.
- Missing task references use the existing fixed `MissingRecord`; immutable-state violations use `SubmissionConflict`. Connections inherit the existing bounded connection behavior.
- `_connect` converts driver `OperationalError` text to the fixed message `database connection failed`, keeps the same exception family, and suppresses the original exception chain so a DSN cannot reappear in a rendered traceback.

## TDD and integration evidence

- RED: missing `pipe_inspector.storage.inspection` prevented test collection.
- RED compatibility regressions: run UUIDs were not JSON strings, and a `SKIPPED` checkpoint call was rejected.
- GREEN: `SEWER_RUN_STORAGE_TEST=1 .venv/bin/python -m pytest backend/tests/integration/test_inspection_storage.py -q --tb=no` -> `6 passed in 2.09s`.
- Tests use only a unique test-owned PostgreSQL schema and cover concurrent run creation, config conflict, missing references, call-attempt history, checkpoint replay, failed-transaction rollback, immutable task input, and terminal-state protection.
- Safe-error RED: a simulated driver exception containing a fake secret propagated unchanged.
- Safe-error GREEN: `.venv/bin/python -m pytest backend/tests/test_repository.py -q --tb=no` -> `1 passed in 0.17s`; the test verifies fixed text, same exception type, suppressed cause, and no fake secret in `traceback.format_exception()`.
- Scoped Ruff for all storage-owned implementation and tests -> `All checks passed!`.
- Root separately reported one real Temporal recovery plus VLM-429 retry workflow test passing with this storage extension; this worker did not duplicate that integration run.

## Scope and concern

- Changed only `storage/inspection.py`, `storage/repository.py`'s `_connect` boundary, their two focused tests, and this report. Shared exports, initializer, configuration, services, and credentials were not modified; no VCS command or history mutation was performed.
- PostgreSQL was initially down. The first failed pytest run expanded the configured DSN in its traceback before output suppression and the repository fix were added; no credential is reproduced or persisted here. Password rotation was not performed because it requires separate authorization.
