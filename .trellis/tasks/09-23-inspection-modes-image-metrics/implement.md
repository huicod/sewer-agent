# 图片模式与离线评测 Implementation Plan

**Goal:** Expose a real persisted image inspection choice and reproduce cached image-level results.
**Architecture:** Existing API → persisted task → frozen run → optional review; independent offline cached scorer.
**Tech Stack:** Python/Pydantic/PostgreSQL/Temporal, React/TypeScript, pytest/Vitest.
**Spec:** prd.md and design.md in this task. Trellis controls execution; use TDD and independently scoped agents. Main agent alone owns jj and journal. No remote writes or paid calls.

## Backend (backend non-evaluation code + tests)

- [x] Write failing tests for `inspection_mode="yolo_only"` on task/turn persistence and same-request mode conflict, frozen runs, no reviewer invocation, no empty-result NORMAL, old default, explicit unsupported video/tools.
- [x] Add public literal/default and propagate from conversation/HTTP through storage/admission/run. Add detector-only decision and truthful evidence/report projection. Skip VLM activity/config in pure mode without altering historical default executions.
- [x] Run focused suites: `PYTHONPATH=. .venv/bin/python -m pytest backend/tests/test_inspection_modes.py -q` plus touched workflow/storage suites. 20 focused passed; opt-in PostgreSQL tests added, not run with database unavailable.

## Frontend (frontend only)

- [x] Write failing tests for both submit paths, saved mode display, retry identity preservation and invalid video choice.
- [x] Project optional wire `inspection_mode` for historical fixtures, default `yolo_vlm`; add labelled selector, payload/retry identity, persisted task summary and skipped-review copy.
- [x] Run `.tools/node/bin/npm --prefix frontend test` and build with `.tools/node/bin` prepended to PATH. Root final verification: 138 passed + build/typecheck passed.

## Cached evaluation (main agent: evaluation/image_level.py + test_image_level_evaluation.py)

- [x] Test duplicate labels, disjoint boxes same class, SEDD unannotated-class mask, empty labels, failed reviews retained, absent/error confirmation rejected and exact historical count fixtures. 14 tests passed after observed missing-module/native-label failures.
- [x] Implement `evaluate_cached(state, confirmation=None)` and `python -m pipe_inspector.evaluation.image_level --state PATH [--confirmation-state PATH] --output PATH`. Output micro/per-class/by-source/image details and IoU=.5 reference; reject malformed input instead of dropping images.
- [x] Recompute saved 60-image experiment without API; compare all published counts and retain output path in report. All 12 source/mixed, 20 per-class, 4 box rows match.

## Integration

- [x] Main reviews diff; one independent read-only review of mode propagation and cached denominators, fix concrete findings. Saved-task selector inheritance corrected; final frontend/evaluator/backend review has no remaining findings.
- [x] `.venv/bin/ruff check backend`; `.venv/bin/python -m pytest backend/tests -q`; frontend tests/build; diff whitespace check. 1215 passed / 162 explicitly skipped; no services/API started.
- [x] Record implementation/verification in report, task status and huicod journal. Keep calibration task pending for human calibration; do not imply model gain from metric change.
