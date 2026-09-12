# T12-C Adaptive Enhancement Presentation Report

## Scope delivered

- `backend/src/pipe_inspector/api/inspection.py` reads the optional
  `enhancement_selection` checkpoint, accepts only the exact `{"selection": ...}`
  wrapper, validates the public `EnhancementSelection`, and returns `selection=null`
  when the checkpoint is absent.
- `backend/src/pipe_inspector/contracts/reports.py` adds optional
  `TaskReport.enhancement_selection`; older report payloads continue to default it
  to null.
- `backend/src/pipe_inspector/api/reports.py` projects the same validated selection
  into JSON and HTML exports without exposing run configuration or server model
  paths.
- `backend/src/pipe_inspector/reports.py` renders selected/skipped reasons,
  degradation evidence, inspection impact, and localized low-light labels. A
  skipped selection renders no comparison image; explicit T12-B enhancement
  reports remain valid when selection is null.
- `frontend/src/types.ts` and `frontend/src/validators.ts` project the full public
  selection schema, add `low_light`, enforce selected/options/proposal agreement,
  reject undocumented fields, accept `selection: null`, and tolerate legacy detail
  responses that omit `selection`.
- `frontend/src/EnhancementPanel.tsx` shows pending, selected, and skipped states in
  the existing on-demand panel. Selected low-light work is localized as
  “低照度恢复”; skipped work shows its reason and proposal evidence without an
  output image. Existing stale-request invalidation still covers task switches and
  panel collapse.
- `frontend/src/App.tsx` maps `active_tool="enhancement_selection"` to an adaptive
  selection label and adds one concise natural-language opt-in example in the
  existing conversation help text. No setting UI was added.

No workflow, storage, selection policy, model adapter, dependency, service, or VCS
state was changed by this presentation slice.

## TDD evidence

- Backend red: `.venv/bin/python -m pytest
  backend/tests/test_adaptive_enhancement_api.py -q` -> `5 failed`; the selection
  checkpoint, report field, and skipped HTML state were absent.
- Report route red: `.venv/bin/python -m pytest
  backend/tests/test_reports.py::test_json_report_preserves_stored_evidence_and_unresolved_result
  -q` -> `1 failed`; the route returned `enhancement_selection: null` instead of
  the stored selection.
- Frontend red: `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix
  frontend test -- src/enhancement.test.tsx src/App.test.tsx` -> `7 failed, 20
  passed`; the new schema, panel states, active-tool label, and help example were
  absent.
- Focused green: `.venv/bin/python -m pytest
  backend/tests/test_adaptive_enhancement_api.py backend/tests/test_enhancement_api.py
  backend/tests/test_reports.py -q` -> `19 passed`.
- Focused frontend green: the same focused Vitest command -> `27 passed`.

## Final verification

- `.venv/bin/python -m pytest backend/tests -q` -> `828 passed, 116 skipped`.
- `.venv/bin/python -m ruff check backend` -> `All checks passed!`.
- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test`
  -> `9 passed` files, `69 passed` tests.
- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend run
  build` -> TypeScript and Vite production build succeeded; 21 modules transformed.

## Files changed by this slice

- `backend/src/pipe_inspector/api/inspection.py`
- `backend/src/pipe_inspector/api/reports.py`
- `backend/src/pipe_inspector/contracts/reports.py`
- `backend/src/pipe_inspector/reports.py`
- `backend/tests/test_adaptive_enhancement_api.py`
- `backend/tests/test_enhancement_api.py`
- `backend/tests/test_reports.py`
- `frontend/src/App.tsx`
- `frontend/src/App.test.tsx`
- `frontend/src/EnhancementPanel.tsx`
- `frontend/src/enhancement.test.tsx`
- `frontend/src/types.ts`
- `frontend/src/validators.ts`
- `.trellis/tasks/09-13-m4-adaptive-enhancement/presentation-report.md`

## Remaining acceptance

- Automated tests verify the DOM states, schema boundary, HTML escaping, JSON
  projection, and build. Browser visual acceptance was not run; final integrated
  workflow/storage/model acceptance remains with the root agent.
