# Open-scan integration evidence

Date: 2026-09-11

## Scope

`backend/tests/integration/test_open_scan_workflow.py` uses the repository's configured
loopback PostgreSQL and Temporal services, an isolated per-test schema, and a short local
FFmpeg-generated video. The primary detector, VLM reviewer, and open detector are deterministic
in-process substitutes; no model file is loaded, no GPU is used, no API is called, and no download
is attempted. The suite is opt-in with `SEWER_RUN_OPEN_SCAN_TEST=1`.

The pinned open configuration exercised by every case is:

- checkpoint: `fixtures/open.pt`
- text encoder: `fixtures/encoder.ts`
- detector/model/vocabulary: `yoloe` / `open.pt` / `sewer-open-v1`

## Covered behavior

1. A real `ImageInspectionWorkflow` executes detect → verify → open selection → GPU open scan →
   finish → human wait. An unframed PL candidate is persisted as `open_detector` evidence needing
   review. A fault injected after the first durable `open_scan:0` commit causes Temporal to retry
   the Activity, while the immutable checkpoint prevents a second open-model call. Human
   confirmation then completes the workflow with `defects_found`.
2. A six-frame synthetic video is scanned and its periodic negative-audit frame is materialized by
   the existing video evidence path. Open scan consumes that saved image asset—not the source video
   and not every decoded frame. An empty open batch preserves the fully audited `normal` result.
3. An explicit video open audit with `per_event_open_scan=0` persists an incomplete empty
   `open_plan`. Final fusion clears `negative_audit_complete` and yields `waiting_review` /
   `inconclusive`, so a budget/evidence gap cannot become a normal conclusion.

## Red → green record

Initial targeted run:

```text
SEWER_RUN_OPEN_SCAN_TEST=1 .venv/bin/python -m pytest \
  backend/tests/integration/test_open_scan_workflow.py -q --tb=short
2 passed, 1 failed
```

The failure was in the new test assertion: it passed the persisted JSON dictionary directly to
`MediaStore.path_for` instead of validating it as `AssetRef`. After correcting the test boundary,
the same command reported:

```text
3 passed in 7.85s
```

Targeted lint was then run with:

```text
.venv/bin/ruff check backend/tests/integration/test_open_scan_workflow.py
```

The first lint pass found import ordering only. Fresh final verification reported:

```text
All checks passed!
3 passed in 7.93s
```

Without the opt-in environment variable, collection is safe and the same file reports `3 skipped`.
