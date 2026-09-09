# T03 implementation evidence

Date: 2026-09-09. jj change: `msmpusxk`; parent T02 checkpoint: `e52516ab`. Implementation integrated; independent review approved with no findings.

- Added shared ContractModel/ImageRegion, UUID/version asset metadata, evidence-backed Finding, TaskSnapshot, constrained TaskPlan and ToolCall. Public exports are available from `pipe_inspector.contracts`; contract imports do not execute models or contact services.
- Categories and review/runtime/conclusion statuses remain separate. OTHER confirmation requires human provenance; normal requires coverage, quality, completed negative audit and no unresolved or confirmed findings. Confirmed defects can coexist with partial coverage. These checks validate snapshots, not workflow transitions or the authenticity of a claimed human identity; the future service/review endpoint owns those writes.
- Plans require primary detection, valid ordered dependencies and budgets; relative measurement requires a segment ancestor. Tool calls preserve asset/model/version, stable business key, attempts and structured failure/uncertain/skip information. No dispatcher or second scheduler was added.
- DetectorAdapter now includes immutable capabilities and explicit load/warmup. A vendor-boundary fake and an independent test adapter use the same consumer. Shared original-pixel geometry validation replaces the old duplicated bbox check.

## Tests

- Root RED: missing assets module before new contracts; an additional two-case RED confirmed the legacy Detection contract silently accepted path/command fields. Common strict extra-field validation then made both pass.
- Root focused GREEN: inspection contracts + prior detector tests → 61 passed in 1.65s.
- Planning/tool tests: 47 passed; detector tests with lifecycle: 23 passed. Details in the two subtask reports.
- Integrated backend: `.venv/bin/python -m pytest backend/tests -q` → **145 passed, 3 skipped in 2.51s**.
- `.venv/bin/ruff check backend scripts/runtime.py scripts/detector_smoke.py` → **All checks passed!**
- Skips remain opt-in live LLM/VLM and service restart tests; no repeated GPU or paid API calls during T03 tests.

Independent review checked the complete T03 diff against the brief and found no missing requirements or out-of-scope implementation. Detector replacement and stable retry-key fixture corrections were included; review did not duplicate the test run.

## Current boundary / next

The trusted YOLO26 GPU smoke passed this turn (AJ, score0.931791); T02 remains open for real provider configuration. T03 has no upload route, business storage, production InspectionWorkflow, frontend or language parser yet. The next planned implementation is T04 upload/media validation, persistent task API/progress and the minimal Web shell; no additional models are required for that step.
