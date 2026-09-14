# T15-E Implementation Plan

> Use superpowers:subagent-driven-development. Trellis alone owns task state; jj overrides Git mutation recipes.

**Goal:** Verify image human wait and video chunk recovery across real service restart.
**Architecture:** test-owned subprocess, existing workflows, isolated PG schema/queues, external model substitutes.
**Tech Stack:** existing Python/pytest/Temporal/psycopg/FFmpeg.
**Spec:** prd.md, design.md; DEVELOPMENT_PLAN.md T15.

## Global Constraints

- No detector/VLM/prompt/fusion changes, paid APIs, hashes, new dependencies, or pushes.
- Only main agent controls jj/shared services. Kill and cleanup exact test-owned resources only.
- Trellis is the sole ledger; one combined independent review, no nested agents.

## Single deliverable: business restart acceptance

Create `backend/tests/business_restart_worker.py`, `backend/tests/integration/test_business_service_restart.py`; optionally `backend/tests/business_restart_support.py` and distinct `backend/tests/test_business_restart_guards.py` for meaningful guards. Main agent owns README/spec/roadmap/Trellis.

- [x] Implementer reads recovery/image/video tests; guard tests before helper implementation. Missing opt-in, active app or foreign workflow refuses before mutation.
- [x] Test worker has deterministic detector/reviewer, file-backed trace, first-chunk gate. Preserve production interfaces, HumanReviewStarter workflow IDs and heartbeat timeouts.
- [x] One dual-gated scenario: assert literal 2 frames/1 block before, 6 frames/3 blocks after; image snapshot/call IDs unchanged and review idempotent. Record actual process/workflow IDs and bounded recovery timings; save before cleanup.
- [x] Main reads full code and checks cluster/app/workflows, stops empty app, runs scenario, restores app even on failure.
- [x] Full backend pytest and Ruff, independent combined spec/quality review alongside artifact inspection.
- [x] Save report, checklist/task status/Session 33; jj checkpoint and new empty change, no push.

Preflight consistency: one test deliverable consumes existing production interfaces. Worker/test share explicit subprocess arguments and trace; safety agrees with design. Writes split between implementer tests and main docs, no interface conflict.
