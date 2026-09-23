# Backend image inspection modes

Implemented the image-only `inspection_mode` contract and propagation for task and
conversation submissions/records. Missing historical fields default to `yolo_vlm`; the
selected mode participates in persisted request identity and is frozen into new run config.

`yolo_only` uses the same detector adapter/pins, omits VLM configuration and review pins,
does not schedule `verify_image`, and cannot be overridden by `force_review`. Its persisted
plan omits the verify step. Video, enhancement, geometry, supplement, and video-frame tool
admission are rejected before their model/tool work; direct task storage also rejects pure
video submissions. Existing/default YOLO+VLM workflow and old run histories without the
field retain their prior behavior.

Detector-only policy preserves each original box, label, and score. Adequate-quality
detections are confirmed with `confirmed_by=detector_only_rules`, explicitly distinct from
VLM confirmation. Zero detections finish `completed` + `inconclusive`, with
`negative_audit_complete=false`, no findings, and no human-review wait. Image evidence adds
`inspection_mode` plus `review_status` (`completed`, `skipped_by_mode`, or `unavailable`),
and JSON/HTML reporting explicitly names pure YOLO and the intentionally skipped VLM review.

Touched backend implementation:

- contracts for tasks, turns, findings, and image evidence
- conversation planning/dispatch and start/admission guards
- image workflow/activities plus detector-only policy
- task/supplement persistence and evidence/report projection
- focused unit tests and opt-in PostgreSQL persistence tests

Validation:

- `backend/tests/test_inspection_modes.py`: 20 passed during the final focused gate
- affected conversation/frame/supplement recovery suites plus focused modes: 92 passed
- earlier contract/start/workflow/evidence/report/API set: 253 passed
- earlier enhancement/geometry/frame/supplement/comparison set after the replay fix: 250 passed
- scoped Ruff over all touched backend/test files: passed
- opt-in PostgreSQL persistence files collect successfully: 20 skipped because
  `SEWER_RUN_STORAGE_TEST` is unset and local PostgreSQL is unavailable
- no live Temporal, network, VLM, detector/GPU, or paid API checks were run
