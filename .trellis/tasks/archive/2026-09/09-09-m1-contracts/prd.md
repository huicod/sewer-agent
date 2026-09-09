# T03 — Unified inspection contracts

Source: DEVELOPMENT_PLAN.md T03; main architecture 10.1, 10.4, 10.5, 10.8. User approved continued implementation. T02 real GPU inference and persistence passed; live API configuration is pending and does not prevent pure-contract work.

Goal: shared validated Python/JSON contracts for media references, findings, task plans/progress and tool calls, consumed by the upcoming API and Temporal activities.

Scope: contracts/{common,assets,findings,tasks,planning,tools}.py, existing detection.py as needed to reuse geometry validation, DetectorAdapter metadata/lifecycle, related tests. No upload endpoints, storage engine, production workflow, LLM prompt planner or new models in T03.

Acceptance: JSON round trips; reject invalid IDs/versions/geometry and unexpected executable/path fields; separate five classes/OTHER/review/runtime/conclusion; OTHER needs human confirmation; normal cannot coexist with unresolved findings or inadequate audit/coverage/quality; a plan cannot omit primary detection, name an unknown tool, contain unresolved/cyclic dependencies or exceed its declared budget; real and test detector implementations share the same protocol. No network/GPU I/O in contract validation.

Write isolation: root owns common/assets/findings/tasks plus integration tests, exports and task state; planning implementer owns planning.py/tools.py plus test_planning_contracts.py; detector implementer owns vision/detector.py and test_detector_lifecycle.py. Only root mutates jj. Independent task-scoped review after integration.
