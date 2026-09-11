# Open-scan contracts and additive fusion report

## Scope

Implemented only the assigned policy slice:

- `backend/src/pipe_inspector/contracts/open_scan.py`
- `backend/src/pipe_inspector/agent/open_scan.py`
- `backend/tests/test_open_scan_policy.py`
- `.trellis/tasks/09-11-m3-open-candidates/reports/open-policy.md`

No adapter, settings, workflow/runtime, storage, dependency, or existing-contract files were
modified. No Git or jj mutation was performed.

## Implemented behavior

- Added frozen Pydantic contracts and the exact `open-scan-v1` / `sewer-open-v1` constants.
- Bounded batches to 100 candidates and validated finite scores/coordinates, original-image box
  bounds, positive dimensions, and paired non-negative video frame/timestamp locators.
- Added deterministic `merge_open_candidates(...)` fusion that preserves every existing finding,
  appends only distinct candidates as `open_detector` / `needs_review`, and keeps `OTHER` named and
  unconfirmed.
- Deduplication is limited to the same asset ID/version, frame, canonical class, IoU >= 0.5, and a
  non-rejected existing finding.
- Stable UUID5 IDs are collision-aware so an identical candidate may reappear after rejection
  without duplicating a finding ID; repeated fusion remains idempotent.
- Incomplete scans clear only `negative_audit_complete`, force `needs_review`, and downgrade
  `normal` to `inconclusive`; other existing results and coverage fields remain unchanged.

## TDD evidence

Initial RED:

```text
.venv/bin/pytest backend/tests/test_open_scan_policy.py --tb=short
collected 0 items / 1 error
ModuleNotFoundError: No module named 'pipe_inspector.agent.open_scan'
```

Initial GREEN after the minimum implementation:

```text
.venv/bin/pytest backend/tests/test_open_scan_policy.py --tb=short
collected 34 items
34 passed in 0.25s
```

Review-found lifecycle regression RED:

```text
.venv/bin/pytest backend/tests/test_open_scan_policy.py::test_rescan_after_rejection_gets_a_distinct_stable_finding_id --tb=short
1 failed in 0.28s
AssertionError: rescanned finding UUID equaled the rejected finding UUID
```

Collision-aware UUID GREEN:

```text
.venv/bin/pytest backend/tests/test_open_scan_policy.py::test_rescan_after_rejection_gets_a_distinct_stable_finding_id --tb=short
1 passed in 0.22s

.venv/bin/pytest backend/tests/test_open_scan_policy.py --tb=short
35 passed in 0.25s
```

Final verification:

```text
.venv/bin/pytest backend/tests/test_open_scan_policy.py --tb=short
35 passed in 0.26s

.venv/bin/ruff check backend/src/pipe_inspector/contracts/open_scan.py backend/src/pipe_inspector/agent/open_scan.py backend/tests/test_open_scan_policy.py
All checks passed!
```

The required integrated `.venv/bin/ruff check backend` was also run. It reported 57 violations,
all in concurrently owned files outside this slice (`config.py`, adapter/runtime modules, and their
tests). Those paths were not modified here; the root agent was notified for integration.

## Remaining integration boundary

The root agent owns runtime selection, activity/workflow wiring, persistence, and integrated review.
This report makes no claim about those unmodified paths or real-model accuracy.
