# Backend conventions

- Current code: Python 3.12, `backend/src/pipe_inspector`; tests: `backend/tests`.
- Configuration: `config.Settings`, server-owned environment prefixed `SEWER_`. Use SecretStr for credentials; never log settings dumps, authorization headers, or media bytes.
- FastAPI factories perform no model loading or network calls during import. `/health` means process liveness, not model/service readiness.
- Models must be behind adapters. Domain contracts cannot import Ultralytics types. Existing five classes only; OTHER needs review, normal is a task conclusion.
- Temporal orchestrates; network, file, DB and GPU operations are Activities. No second custom retry scheduler or business state machine competing with Temporal.
- Open-detector supplementation is retired online (2026-09-12): no new open_scan run pins or plan admission. Keep historical workflow commands/schemas; production registers read-only retired handlers, never the archived OpenScanActivities executor.
- Test behavior before implementation. Unit tests use local fixtures; live model/API/service checks are explicit integration tests, never silently simulated as successes.
- Validation: `.venv/bin/python -m pytest backend/tests -q` and `.venv/bin/ruff check backend`.
- Add only needed modules and dependencies. No routine file hashes, microservice scaffolding, new model training, or unrelated cleanup.
- Shared public contracts: `pipe_inspector.contracts`; use validated constructors/JSON parsing, not `model_copy(update=...)` for untrusted changes (Pydantic bypasses validation there).
- `TaskPlan.target_labels` is reporting focus, not a detector class filter. Runtime gating and transitions remain in the upcoming workflow.
- `ToolCall.idempotency_key` identifies a logical operation across attempts; record `attempt` separately. Database uniqueness/atomic commits must enforce this in the storage layer.
- Intake uses contracts/intake.py; initial submissions reference original image/video AssetRef IDs. The HTTP layer owns media upload and never accepts client filesystem paths.
- PostgresRepository uses short psycopg transactions and explicit init_storage.py DDL; no schema mutation during request/import. UUID request_id is unique and binds one immutable submission across retries.
- MediaStore preserves original bytes, uses Pillow for JPEG/PNG and bounded, file-only FFmpeg for video metadata/first-frame admission. Full video integrity/coverage belongs to later inspection.
- Run storage integration explicitly with SEWER_RUN_STORAGE_TEST=1; each test uses and drops only its unique test-owned schema. Do not restart Temporal/PostgreSQL for intake tests.

- Shared DASHSCOPE_API_KEY is an explicit exception to the SEWER_ prefix: reuse only for explicitly configured Qwen roles on allowlisted official HTTPS hosts. Other providers need explicit role keys; never dump configuration secrets.
- OpenAICompatibleClient response_model validates locally; it does not transmit JSON Schema. Structured parsers must include their schema in the actual messages, with a captured-request test.
- Enhancement is original-image-only and one kind per run. Default auto_enhancement is false; current-turn opt-in admits a separate VLM tool selection after original detect/verify. Persist choice/skip before GPU execution, derive pins from admitted models, and never interpret downstream selection failure as failure of an already saved original review. Explicit linked supplements freeze parent evidence and share the existing slot; auto supplements/video enhancement are not admitted. Atomic save_enhancement commits asset/call/checkpoint together. Auxiliary review preserves original boxes/classes/scores/coverage, sends conflicts/new anomalies to review, and must not turn a required-tool failure into a completable human gate. Retired detectors remain disabled.
