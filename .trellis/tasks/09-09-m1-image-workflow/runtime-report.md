# T05 image workflow — local validation passed, live VLM pending

## Delivered

Explicit image start and calls APIs; immutable run configuration; JSON-boundary Temporal workflow; checkpointed original-image detection/quality, conditional VLM review and evidence decisions; separate serial-GPU / CPU-API workers; page startup action, progress, conclusions and evidence links. Text is stored but T06 parsing/dialogue is not yet implemented. Video start returns 409; unresolved findings currently end partial, with T09 human resolution still pending.

## Final verification (2026-09-10)

- `SEWER_RUN_STORAGE_TEST=1 SEWER_RUN_IMAGE_WORKFLOW_TEST=1 .venv/bin/pytest -q backend/tests --tb=short`: **248 passed, 4 skipped in 15.41s**. Skipped: separately opt-in GPU, two paid live-API tests, shared-service restart test.
- Ruff on backend and runtime/detector/inspection/init scripts: **All checks passed**.
- Frontend project Node: **15 tests passed**, TypeScript/Vite production build passed.
- Separate trusted GPU-through-production-workflow test: **1 passed in 5.82s**; 720×576 original input, AJ score >0.9, completed/defects_found, detect succeeded and conditional verify skipped. Only the already-authorized checkpoint was loaded.
- Real local Temporal/PG tests cover CPU worker restart with cached detection, VLM 429 retry, duplicate HTTP start and saved result/calls, lost detection completion after DB commit, and one failed final projection. For the last fault test, detect attempts=[1,2], finalize attempts=[1,2], model=1 and VLM substitute=1; final finding IDs/payload remain unchanged.
- Independent backend and UI spec/quality review: **PASS**, after two recovery fixes and one UI enum-validation fix; see review.md. Final writes are recoverably convergent, not globally exactly-once.

## Runtime and handoff

Existing PostgreSQL/Temporal were initially stopped and restored with the existing runtime script; both remain local and ready. Business table initialization passed. CPU and GPU CLI workers each printed ready in a bounded startup probe and were stopped (expected timeout exit 124); no web/API/inspection worker is being left running by this turn. No new dependencies, file hashing, model training, direct Git commit commands or GitHub pushes; local changes are saved by jj.

T05 remains in progress only for real VLM configuration and controlled sample acceptance; no API call or accuracy-improvement claim was made. Candidate: qwen3-vl-plus; alternate qwen3-vl-flash. Provider/model ID/region/Base URL may be discussed, but keys belong only in server .env. Run instructions are in backend/README.md; after live acceptance continue T06.

A failed initial local PostgreSQL test expanded a DSN in tool output; no credential was copied to reports/Git and no external API key was involved. The connection boundary now redacts such failures. Local app-password rotation was not performed; it remains a security follow-up requiring coordination.
