# T06-B1 storage report

## Scope delivered

- Added immutable public `TurnInput`, `TurnSubmission`, and `TurnRecord` contracts with bounded text/error fields, aware timestamps, state literals, defaults, and terminal-state validation.
- Added `ConversationRepository(InspectionRepository)` and `ConversationBusy`.
- Added explicit initialization for `sewer_conversation_turns`; importing the module performs no schema or connection work.
- Added durable admission, reads/listing, immutable first config, per-session serialization, task/asset ownership checks, constrained updates, and first-writer checkpoints.
- Kept changes to the five files authorized by `storage-brief.md`. API/workflow/UI/bootstrap wiring remains owned by the root agent.

## Focused TDD evidence

RED:

- `.venv/bin/pytest -q backend/tests/test_turn_contracts.py` failed collection with `ModuleNotFoundError: pipe_inspector.contracts.turns` before the contract implementation existed.
- `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_conversation_storage.py` failed collection with `ModuleNotFoundError: pipe_inspector.storage.conversation` before the repository implementation existed.
- The first combined PostgreSQL run exercised the implementation and exposed an escaped-JSON-literal defect in the composed `CREATE TABLE` SQL. It failed all nine integration setups at that exact statement; the SQL literal was corrected before GREEN.

GREEN:

- `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_conversation_storage.py backend/tests/test_turn_contracts.py` passed: `28 passed in 3.93s`.
- `.venv/bin/ruff check backend/src/pipe_inspector/contracts/turns.py backend/src/pipe_inspector/storage/conversation.py backend/tests/test_turn_contracts.py backend/tests/integration/test_conversation_storage.py` passed: `All checks passed!`.

The PostgreSQL tests create a unique `test_conversation_storage_<uuid>` schema per test, reopen repositories against it, and drop only that schema. They cover request retry/mismatch, concurrent same-session admission, separate sessions, reference ownership, chronological tail reads, terminal stability, attempts monotonicity, linked-task ownership, sequential and concurrent checkpoint first-write behavior, and credential-safe error surfaces.

## Handoff notes

- Duplicate `request_id` with identical submission returns the stored record and preserves the first config even when the retry supplies a different config.
- A queued/running turn makes only its own session busy; terminal records are stable and ignore later update attempts.
- Asset ownership is inferred from existing task use: an original asset already referenced by a task in another session cannot be admitted into this session.
- No full-suite run was performed; integration and full-suite verification remain with the root agent as requested.
