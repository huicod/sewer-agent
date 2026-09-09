# T02 detector adapter report

## Delivered

- `contracts/detection.py`: frozen Pydantic values for the five canonical labels,
  native label, finite score in `[0, 1]`, original-pixel `xyxy`, and batch metadata.
- Batch validation rejects non-finite, unordered, negative, and out-of-image boxes.
- `vision/detector.py`: `DetectorAdapter` protocol and one `UltralyticsDetector`.
  The model is imported/loaded lazily, the checkpoint and every image must exist,
  and prediction labels are translated through the loaded model's actual
  `model.names` map.
- A loaded model must expose exactly `AJ`, `BX`, `FS`, `PL`, and `BLOCK`; no
  `NORMAL` or `UNCERTAIN` object labels enter the detection contract.
- No global model instance, training, model download, tracking, hashing,
  alternate detector, or implicit prediction-output files were added.
- `scripts/detector_smoke.py` requires explicit image, checkpoint, and device,
  prints contract JSON, and writes it only when `--output` is supplied.

## Deterministic verification

```text
.venv/bin/python -m pytest backend/tests/test_detector.py -q
18 passed in 1.61s

.venv/bin/python -m ruff check backend/src/pipe_inspector/contracts \
  backend/src/pipe_inspector/vision backend/tests/test_detector.py \
  scripts/detector_smoke.py
All checks passed!
```

The tests cover the canonical happy path, forbidden labels, invalid scores,
coordinate finiteness/order/bounds, lazy one-time model loading, non-numeric-order
`model.names` translation, invalid trained class maps, missing input before
model loading, and missing checkpoints on every lazy-load entry point. The CLI `--help` path also completed without loading weights.

## Real CUDA smoke status

- Environment import check: Ultralytics `8.4.46` is installed in `.venv`.
- Requested input: ignored `var/fixtures/sewer2403-sample.jpg`, a real 720x576
  archive image whose VOC annotation contains one `AJ` object.
- Requested checkpoint: `yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt`.
- The execution tool rejected the smoke before the process started because this
  `.pt` is a pickled full model and Ultralytics/PyTorch loads it with
  `weights_only=False`, which can execute embedded code. The exact command and
  rejection were given to the main agent for an explicit trust decision.
- Therefore `var/fixtures/detector-smoke.json` was not created, and no GPU
  inference, detection count, elapsed time, or prediction output is claimed.
