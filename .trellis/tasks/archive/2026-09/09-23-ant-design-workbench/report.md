# Delivery evidence — Ant Design inspection workbench

Date: 2026-09-23. User authorized Ant Design and a business-workbench frontend. jj change: `uwpsyylp`, parent `d3936db9`.

## Delivered
- Ant Design 6 / Chinese theme; restrained light surfaces, blue actions, compact typography.
- Searchable/filterable task navigation, one focused inspection, collapsible assistant, manual intake modal with retained drafts.
- Extracted task detail with source media, findings, execution state, on-demand evidence/tools, human review and reports.
- Responsive navigation with keyboard focus, Escape and hidden closed controls; stable explicit textarea labels.
- Visible inspection and assistant target remain independent, including after message admission.
- Scope is frontend plus tests and Trellis records. Backend contracts, stored data and provider configuration were unchanged.

## Verification
- 17 frontend unit suites / 145 tests passed (`var/evaluations/ant-design-workbench/unit-verified.log`). After the final mobile-only focus adjustment, its real-browser scenario was rerun successfully.
- New navigation regression was observed failing before the focus-reset correction, then the affected suites passed 18/18 (`focus-regression-red.log`, `review-fixes.log`).
- Three synthetic browser scenarios passed across focused runs: workbench/modal/responsive keyboard navigation (`browser-layout-final.log`); image/video, saved review/evidence/report/comparison and lost-admission retry (`browser-verified.log`, the initial responsive failure in that run is superseded by the focused final pass).
- Desktop and mobile screenshots inspected. Additional populated-task check at 1100px and 390px: no page overflow or page errors (`visual/layout-results.json`).
- Independent code review completed; corrected unconditional displayed-task reset and inaccessible hidden navigation. No unresolved material findings.
- Production TypeScript/Vite build passed (`production-build.log`); stop → build → start executed sequentially. API, CPU, GPU, PostgreSQL and Temporal all ready at `http://127.0.0.1:8000`. Root, health, JS and CSS return HTTP 200 (`http-verified.json`).
- Diff whitespace check passed. No commits/pushes or real paid model requests were made.

## Limits / follow-up
- Browser workflow verification used synthetic model/media adapters with real HTTP/storage/workflows. It does not measure real-model inspection quality or substitute for user visual acceptance.
- Vite reports the main JS chunk at 632 kB (199 kB gzip), above its advisory 500 kB threshold after adding Ant Design. Build succeeds; no unrequested framework or bundler restructuring was added.
- User can inspect the served build through the existing port forward to localhost:8000.
