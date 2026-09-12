# Frontend conventions

- Current package: frontend/, React + TypeScript + Vite. Keep UI state local until a concrete shared-state need appears; no generic chat framework.
- Backend-owned contracts are projected in src/types.ts; src/api.ts owns the network boundary. Components do not embed server URLs/credentials or create runtime conclusions.
- Use same-origin /api via the local Vite proxy. Development binds 127.0.0.1; no production authentication or public deployment is implied.
- Session ID may be saved in browser localStorage; authoritative task/evidence data stays on the server. Reload restores a session, not a new inspection.
- Submission retries reuse request_id and the uploaded asset only for the same selected File and instruction/session. File names, sizes and timestamps are not content identity; no file hashing.
- Display real server stages/progress/errors; T04 queued means accepted but not executed. A chat-style page must not invent an assistant response.
- UI tests: npm --prefix frontend test; build/typecheck: npm --prefix frontend run build. This server needs .tools/node/bin on PATH, not its older system Node.
- Component tests use explicit HTTP-boundary responses; real backend/database integration is verified separately. Passing a DOM test is not visual browser acceptance.
- Enhancement evidence is an on-demand task panel; invalidate requests on task switch/close, refresh expanded panels with server revisions, and distinguish absent output from API failure. Draw ROI in original pixels and label enhanced images as auxiliary evidence, not restored truth or accuracy gains.
- Geometry follows the same on-demand/source/stale-response rules. Display actual mask/overlay and relative-depth assets, original-pixel prompts, finding IDs for dialogue, and explicit full-image denominator/relative units; never reinterpret mask score as defect confidence or render physical dimensions without calibration. Keep it read-only until a separate editor task is approved.
