# Backend conventions

- Current code: Python 3.12, `backend/src/pipe_inspector`; tests: `backend/tests`.
- Configuration: `config.Settings`, server-owned environment prefixed `SEWER_`. Use SecretStr for credentials; never log settings dumps, authorization headers, or media bytes.
- FastAPI factories perform no model loading or network calls during import. `/health` means process liveness, not model/service readiness.
- Models must be behind adapters. Domain contracts cannot import Ultralytics types. Existing five classes only; OTHER needs review, normal is a task conclusion.
- Temporal orchestrates; network, file, DB and GPU operations are Activities. No second custom retry scheduler or business state machine competing with Temporal.
- Test behavior before implementation. Unit tests use local fixtures; live model/API/service checks are explicit integration tests, never silently simulated as successes.
- Validation: `.venv/bin/python -m pytest backend/tests -q` and `.venv/bin/ruff check backend`.
- Add only needed modules and dependencies. No routine file hashes, microservice scaffolding, new model training, or unrelated cleanup.
- Shared public contracts: `pipe_inspector.contracts`; use validated constructors/JSON parsing, not `model_copy(update=...)` for untrusted changes (Pydantic bypasses validation there).
- `TaskPlan.target_labels` is reporting focus, not a detector class filter. Runtime gating and transitions remain in the upcoming workflow.
- `ToolCall.idempotency_key` identifies a logical operation across attempts; record `attempt` separately. Database uniqueness/atomic commits must enforce this in the storage layer.
