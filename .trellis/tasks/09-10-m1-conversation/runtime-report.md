# T06-A validation — 2026-09-10
Historical slice A evidence. Latest B1 and successful Qwen validation: [conversation-report.md](conversation-report.md).

- Final candidate: after the actual-request JSON schema fix, no concurrent code writes.
- Command: `SEWER_RUN_STORAGE_TEST=1 SEWER_RUN_IMAGE_WORKFLOW_TEST=1 .venv/bin/pytest -q backend/tests --tb=short`.
- Result: **304 passed, 4 skipped in 16.13s**. Skips: explicit GPU check, two live API checks, shared-service restart check. These were not claimed as passing in this run.
- `.venv/bin/ruff check backend`: passed. `git diff --check`: passed.
- Includes real local PostgreSQL report readback after reopening repository, and existing real Temporal image workflow/retry/recovery regression. No paid API, GPU weight inference or shared-service restart in this regression.
- One earlier integration run overlapped the parser's deliberate RED window: 303 passed / 1 schema-payload assertion failed. The final run above was after the fix and supersedes it.
- The separately executed Qwen text probe failed authentication, so live model integration remains unresolved. See ../09-09-m1-image-workflow/qwen-api-report.md.
- No frontend files changed in this slice; report HTML is tested at the HTTP/content boundary, not through visual browser QA.
