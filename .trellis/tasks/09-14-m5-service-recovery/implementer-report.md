# T15-E business restart harness — implementer report

## Implemented scope

- Added the four authorized test files only: safety/evidence helpers, offline guard tests,
  the disposable subprocess worker, and the opt-in integration scenario.
- The subprocess registers the real `ImageInspectionWorkflow`, `VideoInspectionWorkflow`,
  production activities, `HumanReviewGate`, `HumanReviewStarter`, and isolated PG repository.
  Only detector/reviewer boundaries are deterministic substitutes; their physical calls are
  appended to `call-trace.jsonl` across both worker processes.
- The scenario requires both restart gates plus a new absolute output below
  `var/evaluations`, rejects a running managed app or foreign workflow, and rechecks the exact
  owned workflow set immediately before interruption. It kills only its test-owned process
  group, uses the unchanged `.venv/bin/python` launcher, preserves the production 30-second
  heartbeat timeout, restarts only managed Temporal and PG 14/main, and performs exact cleanup.

## TDD and offline evidence

- Guard RED: `pytest backend/tests/test_business_restart_guards.py -q` failed at collection
  with the expected missing `business_restart_support` module.
- Guard GREEN: 10 passed.
- The first live single-file invocation found a collection-only import-path defect before the
  output directory, schema, workflows, or service restart existed. Root cause: the earlier
  combined invocation had incidentally placed `backend/tests` on `sys.path`. Package-qualified
  `backend.tests.business_restart_support` and `backend.tests.business_restart_worker` imports
  fixed the boundary.
- Exact single-file default verification then produced 1 skipped; combined guard/default
  verification produced 10 passed, 1 skipped; full backend collect-only found 1210 tests;
  scoped Ruff passed.

## Authorized live evidence

Command (the output path must be new):

```bash
SEWER_RUN_RESTART_TEST=1 \
SEWER_RUN_BUSINESS_RESTART_TEST=1 \
SEWER_BUSINESS_RESTART_OUTPUT=/root/autodl-tmp/server/var/evaluations/2026-09-14-business-restart-001 \
.venv/bin/python -m pytest backend/tests/integration/test_business_service_restart.py -q -s
```

Result: 1 passed in 37.88 seconds. Evidence is retained at
`var/evaluations/2026-09-14-business-restart-001/`.

- Managed Temporal PID changed `2156 -> 118993`; PG postmaster start time changed from
  `2026-09-14T01:09:19.551533+08:00` to `2026-09-14T11:12:11.726054+08:00`.
- Both Temporal workflow IDs and run IDs were identical before and after restart.
- Image detector/reviewer calls stayed at one each; its waiting snapshot, original AJ box,
  checkpoints, and tool-call records were unchanged. Synthetic human review completed and an
  identical submission replayed from the saved audit without reconnecting to Temporal.
- Video state changed from 2 frames/1 chunk to 6 frames/3 chunks. Trace indices were exactly
  `0..5` once each across two worker PIDs; persisted chunks were `[0,1]`, `[2,3]`, `[4,5]`.
- Service restart took 4.79 seconds; recovery after Temporal readiness took 26.11 seconds.
  Cleanup reported no errors and the managed runtime recovered.

No production workflow, dependency, configuration, VCS history, paid API, or shared-service
mutation was performed by the implementer session. The main agent owns full-suite verification,
independent review, app restoration confirmation, and final jj/Trellis integration.
