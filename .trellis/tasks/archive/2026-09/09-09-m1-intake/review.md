# T04 independent review

Date: 2026-09-09. Scope: T04 intake only. Verdict: **APPROVED**.

## Specification verdict — PASS

No blocking specification findings remain.

- The HTTP surface is limited to session, asset and task intake/readback; task creation persists a queued snapshot without claiming inspection or dialogue execution (`backend/src/pipe_inspector/api/intake.py:26`, `backend/src/pipe_inspector/api/intake.py:67`, `backend/src/pipe_inspector/storage/repository.py:179`). The UI states the same boundary explicitly (`frontend/src/App.tsx:49`, `frontend/src/App.tsx:64`).
- Uploads preserve the submitted bytes at a server-generated UUID/version path, enforce the file limit while streaming, fully decode JPEG/PNG, and admit video only after bounded metadata plus first-frame checks (`backend/src/pipe_inspector/media.py:36`, `backend/src/pipe_inspector/media.py:42`, `backend/src/pipe_inspector/media.py:71`, `backend/src/pipe_inspector/media.py:89`, `backend/src/pipe_inspector/media.py:107`). Invalid intake removes only the new asset directory (`backend/src/pipe_inspector/media.py:58`).
- Request bodies are bounded before multipart spooling can grow without limit, including chunked requests; reachable length, multipart and application failures now use the required structured detail shape (`backend/src/pipe_inspector/api/limits.py:16`, `backend/src/pipe_inspector/api/limits.py:42`, `backend/src/pipe_inspector/api/app.py:61`).
- PostgreSQL initialization is explicit, connections are bounded, asset versions are immutable, and task replay returns the original record before reference revalidation while conflicting reuse is rejected (`backend/src/pipe_inspector/storage/repository.py:45`, `backend/src/pipe_inspector/storage/repository.py:48`, `backend/src/pipe_inspector/storage/repository.py:131`, `backend/src/pipe_inspector/storage/repository.py:179`). Session/task/asset reads are persisted repository operations (`backend/src/pipe_inspector/storage/repository.py:110`, `backend/src/pipe_inspector/storage/repository.py:167`, `backend/src/pipe_inspector/storage/repository.py:259`).
- The browser restores its saved session, polls only active task states, and retains the same asset reference and request UUID when retrying the same failed submission (`frontend/src/App.tsx:124`, `frontend/src/App.tsx:150`, `frontend/src/App.tsx:183`). A newly selected `File` object is treated as new evidence. Every successful API response is parsed and validated before use (`frontend/src/api.ts:42`, `frontend/src/validators.ts:55`).

## Code-quality verdict — PASS

The implementation is bounded and cohesive: media admission, HTTP limits, persistence, API routing and UI validation have distinct responsibilities; no credentials, provider calls, Temporal dispatch or speculative model path were introduced.

Recorded evidence is sufficient for this review: 176 backend tests passed with 12 documented skips, Ruff passed, the 9 opt-in PostgreSQL/real-HTTP tests passed separately, 11 frontend tests passed, and TypeScript/Vite production build passed (`.trellis/tasks/09-09-m1-intake/report.md:29`). The structured-error and frontend retry/validation fixes include focused RED/GREEN evidence (`.trellis/tasks/09-09-m1-intake/report.md:33`). I did not duplicate those completed suites during this read-only review.

## Non-blocking boundaries

- Development `StrictMode` can create one unused empty session on a first load with no saved ID (`frontend/src/main.tsx:7`, `frontend/src/App.tsx:124`). It cannot duplicate uploads or task creation, and the production build does not double-run this effect; session lifecycle cleanup is outside T04.
- Bytes are intentionally retained after an ambiguous database failure, so unreferenced originals may require later operational garbage collection (`backend/src/pipe_inspector/api/intake.py:41`). Whole-video integrity, browser visual QA, production supervision and real CCTV effect acceptance also remain explicitly deferred (`.trellis/tasks/09-09-m1-intake/report.md:23`).
