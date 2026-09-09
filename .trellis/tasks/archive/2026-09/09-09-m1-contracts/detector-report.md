# T03 detector lifecycle report

## Change

- Added frozen, slotted `DetectorCapabilities` with `supports_batch: bool` and
  `coordinate_space: Literal["original_pixels"] = "original_pixels"`.
- Extended `DetectorAdapter` with `capabilities`, `load()`, and
  `warmup(image_path)` while retaining metadata, class names, and image/batch
  prediction.
- `UltralyticsDetector.capabilities.supports_batch` is `True`.
- `load()` uses the existing guarded lazy loader. Missing checkpoints fail
  before the Ultralytics vendor loader can download or substitute a model.
- `warmup()` performs exactly one normal prediction on an explicit existing
  representative image and discards the returned contract; it creates no
  prediction output files.
- Added only a test-local independent adapter to demonstrate replacement. No
  second production detector was introduced.

## TDD evidence

RED, before production changes:

```text
.venv/bin/python -m pytest backend/tests/test_detector_lifecycle.py -q
5 failed in 1.54s
```

Failures were the expected missing `DetectorCapabilities`, `load()`, and
`warmup()` behavior.

GREEN, after the minimal implementation:

```text
.venv/bin/python -m pytest backend/tests/test_detector_lifecycle.py -q
5 passed in 1.67s

.venv/bin/python -m pytest backend/tests/test_detector.py \
  backend/tests/test_detector_lifecycle.py -q
23 passed in 1.57s

.venv/bin/python -m ruff check backend/src/pipe_inspector/vision/detector.py \
  backend/tests/test_detector_lifecycle.py
All checks passed!
```

The parameterized consumer exercised canonical AJ output, score, original-image
dimensions and pixel coordinates, capabilities, explicit load/warmup, one-time
vendor loading, and equivalent use of the independent test adapter. Separate
cases cover immutable capabilities and missing-checkpoint guards for both
lifecycle entry points.

## Existing real smoke evidence

The main agent supplied current-turn trusted GPU-smoke evidence; this subtask did
not rerun the checkpoint:

```text
class map: 0=PL, 1=BX, 2=FS, 3=BLOCK, 4=AJ
result: AJ, score=0.93179
```

This is lifecycle/runtime compatibility evidence for one real image, not an
accuracy measurement.
