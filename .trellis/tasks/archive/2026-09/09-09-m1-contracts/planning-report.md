# T03 planning and tool contract evidence

## Scope

- Added `backend/src/pipe_inspector/contracts/planning.py`.
- Added `backend/src/pipe_inspector/contracts/tools.py`.
- Added `backend/tests/test_planning_contracts.py`.
- Reused root-owned `ContractModel`, `NonEmptyStr`, `AssetRef`, and existing `DetectionLabel`.
- Did not edit exports, common/assets contracts, Settings, packaging, workflow code, Git, or jj.

## Implemented planning contract

- `ActionKind` and `OutputFormat` contain only the brief's fixed values.
- `CallBudget` applies the specified defaults and numeric bounds.
- `PlanStep` exposes only ID, action, earlier-step dependencies, and required flag.
- `TaskPlan` keeps target labels as reporting focus while requiring one full-space detect step.
- Validation rejects empty/duplicate target labels or outputs, duplicate step IDs, extra execution
  fields, unknown actions, missing/multiple/misplaced detect, non-required or dependent detect,
  duplicate/forward/self/cyclic/dangling dependencies, steps without a path to detect,
  relative measurement without a segment ancestor, and plans exceeding their call budget.

## Implemented tool-call contract

- Added fixed resource and status enums plus structured `ToolError`.
- `ToolCall` records UUID identity, idempotency key, attempt, version/resource/timeout, assets,
  status, safe error metadata, and skip reason without implementing execution or retries.
- Failed/uncertain calls require errors; queued/running/succeeded prohibit errors; skipped calls
  require a reason and prohibit errors; all other states prohibit skip reasons.
- Failed, skipped, and uncertain records may retain partial output assets.
- Attempts and finite positive timeouts are validated by Pydantic fields.

## TDD evidence

- RED: `.venv/bin/python -m pytest backend/tests/test_planning_contracts.py -q` failed collection
  with `ModuleNotFoundError: No module named 'pipe_inspector.contracts.planning'` before the owned
  production modules existed.
- Root then supplied the real common and asset contracts; no local stubs were created.
- GREEN: `.venv/bin/python -m pytest backend/tests/test_planning_contracts.py -q` returned
  `47 passed in 0.16s` after final formatting.
- Lint: `.venv/bin/ruff check backend/src/pipe_inspector/contracts/planning.py
  backend/src/pipe_inspector/contracts/tools.py backend/tests/test_planning_contracts.py` returned
  `All checks passed!`.

## Notes

- Tests use literal dictionaries and real Pydantic validation; no mocks, credentials, network,
  GPU, or external services are involved.
- No contract ambiguity remains within the supplied planning brief. Full-suite integration and
  export review remain with the root agent by task contract.
