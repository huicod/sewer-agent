# T12-B Presentation Report

## Scope delivered

- `backend/src/pipe_inspector/contracts/reports.py`: added optional enhancement result/review fields to `TaskReport`, preserving old payload defaults and enforcing task ownership plus review/result ordering.
- `backend/src/pipe_inspector/api/inspection.py`: added the read-only enhancement detail loader and `GET /api/tasks/{task_id}/enhancement`; only the public `enhance` and `enhancement_review` checkpoints are read and validated.
- `backend/src/pipe_inspector/api/reports.py`: projects the same validated checkpoints into JSON/HTML reports and permits only same-origin images in HTML CSP.
- `backend/src/pipe_inspector/reports.py`: renders original/derived image links and previews, source ROI, model/version, before/after quality, and escaped auxiliary-review content with an explicit non-verdict warning.
- `backend/tests/test_reports.py`, `backend/tests/test_enhancement_api.py`: cover null, intermediate and complete checkpoint states, cross-task rejection, old-report compatibility, parent-report identity, escaping, CSP and read-only access.
- `frontend/src/types.ts`, `frontend/src/validators.ts`, `frontend/src/api.ts`: added the typed enhancement boundary, strict documented-field validation, nested asset ownership checks and requested-task identity validation.
- `frontend/src/EnhancementPanel.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`: added an on-demand comparison panel, exact original-coordinate ROI overlay, persisted-result/review states, localized enhancement stages/tools, and stale-request invalidation on task switch/collapse.
- `frontend/src/enhancement.test.tsx`, `frontend/src/App.test.tsx`: cover valid/invalid HTTP payloads, path-field rejection, on-demand loading, comparison rendering, errors, switching, reopen/reload and no automatic per-card request.

No workflow, storage, model, service, dependency or VCS write was performed by this presentation slice.

## Red/green evidence

- Backend red: `.venv/bin/python -m pytest backend/tests/test_reports.py backend/tests/test_enhancement_api.py -q` -> `8 failed, 6 passed` because report fields/routes/checkpoint projection were absent.
- Backend green: same command -> `14 passed`.
- Frontend red: `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test -- enhancement.test.tsx App.test.tsx` -> missing `EnhancementPanel` suite plus missing App stage mapping.
- Frontend first green: same command -> `19 passed`.
- Boundary red: focused `enhancement.test.tsx` -> `2 failed, 8 passed` for an undocumented server path and a mismatched returned task ID.
- Boundary green: focused `enhancement.test.tsx` -> `10 passed`.
- Build red/green: the first build identified two TypeScript narrowing/assertion errors; after the narrow fixes, the build completed successfully.

## Final verification

- Focused backend pytest: `14 passed in 1.62s`.
- Scoped Ruff: `All checks passed!`.
- Full frontend Vitest: `9 passed` files, `63 passed` tests.
- Frontend typecheck/build: successful; Vite transformed 21 modules and emitted production assets.

## Risks and follow-up

- Corrupt or cross-task persisted enhancement evidence is deliberately rejected as an internal server error rather than displayed; there is no new public corruption-specific error contract.
- DOM and build verification passed, but no browser visual acceptance was run because this slice was forbidden from starting services.
- Full backend regression, storage/workflow integration and the single independent scoped review remain owned by the root agent as specified.
