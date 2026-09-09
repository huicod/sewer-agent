# T04 implementation plan

Goal: persist file/text submissions associated with sessions and expose a truthful minimal interface.
Spec: design.md. Controlling workflow: Trellis; Superpowers TDD/parallel implementation techniques, with repository jj and scope overrides.

## File ownership and execution

- [x] Verify predecessor T03: archived review approved; current baseline 145 passed, 3 skipped.
- [x] Storage agent: contracts/intake.py, storage/repository.py and __init__.py, scripts/init_storage.py, backend/tests/test_intake_contracts.py and integration/test_storage.py. RED for missing contracts/repository; GREEN real PostgreSQL reconnect, references, idempotency/conflict, concurrent duplicate and session grouping. No other file edits.
- [x] Frontend agent: frontend/** and frontend-report.md only; isolated Node under .tools/node allowed. Read design HTTP contract. RED component/API boundary tests, then Chinese upload/instruction/session/status UI. Verify tests/build; no business status simulation, model/API calls or backend edits.
- [x] Root: config.py, media service, API routes/app, relevant backend tests; install FFmpeg dependency, TDD actual media/streaming limits/path isolation and API with real repository integration.
- [x] Root: minimal main-architecture/roadmap dialogue clarification, backend runbook/env example, fill current Trellis context and actual frontend/backend conventions.
- [x] Integrate: full backend test + opt-in storage integration; Ruff; frontend tests/build; independent T04 scope/quality review, fix only actual findings; record evidence and next task.

Interface scan: storage produces the exact contracts/repository signatures in design.md; root API consumes them; frontend consumes the documented JSON. Files do not overlap. Root alone updates common task state and jj. No secondary ledger, hashes, new model, Temporal restart, remote push, or public service binding.
