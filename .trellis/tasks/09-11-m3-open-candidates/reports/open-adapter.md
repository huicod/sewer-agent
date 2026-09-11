# Open detector adapter report

## Delivered

- Added `OpenDetectorAdapter` and lazy `YOLOEOpenDetector` in `backend/src/pipe_inspector/vision/open_detector.py`.
- Pinned public identity to `detector_id="yoloe"`, `model_version=checkpoint.name`, and `vocabulary_version="sewer-open-v1"`.
- Pinned a seven-concept English sewer-defect vocabulary: `lateral connection opening in sewer pipe` (AJ), `pipe deformation` (BX), `pipe corrosion` (FS), `crack in sewer pipe` (PL), `pipe blockage` (BLOCK), plus `root intrusion` and `water infiltration` as named `OTHER` candidates. No ordinary-object prompts or aliases are present.
- Kept construction free of filesystem/model imports. First prediction validates the upload, local checkpoint, local encoder, and a working local `clip` package before importing Ultralytics text-model code.
- Reports missing or broken local CLIP as an actionable `ImportError`, allowing the workflow to classify deployment misconfiguration as non-retryable without invoking Ultralytics' implicit installer path.
- Loads `YOLOE(..., task="segment")`, creates `MobileCLIPTS` from the explicit path on CPU, obtains cached text embeddings through the installed underlying API, removes the encoder reference, then performs bounded GPU inference (`max_det=100`).
- Converts results to CPU, validates class IDs and original-pixel boxes through `OpenDetectionBatch`, and supports idempotent model/predictor/CUDA release with `unload()`.

## TDD evidence

Red stages:

- Missing adapter: 19 expected behavior failures after creating only an importable empty module.
- Broken-but-discoverable `clip`: targeted test failed because Ultralytics would have been imported before proving `clip` importable.
- Candidate cap: prediction-argument test failed until `max_det=100` matched the contract bound.
- Runtime integration revision: four expected failures proved the old `RuntimeError` classification and AJ/PL semantic prompts remained until the adapter adopted non-retryable `ImportError` and visual-feature prompts.

Green stage:

```text
.venv/bin/python -m pytest backend/tests/test_open_detector.py -q
20 passed in 0.19s

.venv/bin/ruff check backend/src/pipe_inspector/vision/open_detector.py backend/tests/test_open_detector.py
All checks passed!

.venv/bin/python -m pytest backend/tests/test_open_detector.py backend/tests/test_open_scan_runtime.py backend/tests/test_open_scan_policy.py -q
61 passed in 0.81s
```

Tests use local files and lightweight vendor fakes; they do not import the real CLIP package, load a model, require a GPU, change CWD, install, or download.

## Remaining verification

No real-model smoke was run under this bounded task. It still requires the approved official `yoloe-26s-seg.pt`, explicit `mobileclip2_b.ts`, and preinstalled Ultralytics CLIP package. The smoke must verify actual TorchScript compatibility, GPU memory release, and original-frame coordinates. Accuracy and threshold comparison remain T11.
