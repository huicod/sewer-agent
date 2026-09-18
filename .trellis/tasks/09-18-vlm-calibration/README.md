# VLM calibration working set

This task separates validation calibration from final test evaluation.

The detector threshold analysis consumes the already saved YOLO26m validation
export at `var/evaluations/2026-09-17-yolo26m/new_mixed_val/`. It is a
no-API, no-training analysis and does not change the production threshold.

Run it with:

```bash
PYTHONPATH=.:backend/src .venv/bin/python \
  .trellis/tasks/09-18-vlm-calibration/analyze_detector_thresholds.py \
  --data-root var/datasets/merged_five_v1 \
  --prediction-dir var/evaluations/2026-09-17-yolo26m/new_mixed_val \
  --manifest var/datasets/merged_five_v1/split_manifest.csv \
  --output var/evaluations/2026-09-18-vlm-calibration/thresholds.json
```

To create the review sheet with the original detector boxes and scores:

```bash
PYTHONPATH=.:backend/src .venv/bin/python \
  .trellis/tasks/09-18-vlm-calibration/create_calibration_manifest.py \
  --data-root var/datasets/merged_five_v1 \
  --manifest var/datasets/merged_five_v1/split_manifest.csv \
  --prediction-dir var/evaluations/2026-09-17-yolo26m/new_mixed_val \
  --output var/evaluations/2026-09-18-vlm-calibration/calibration_manifest.json
```

The output is a candidate protection policy, not a claim of production
accuracy. Human calibration records remain separate and must be completed
before any VLM addition or class-specific gate is promoted automatically.

Current artifacts are written under
`var/evaluations/2026-09-18-vlm-calibration/`: `thresholds.json`, the
60-sample `calibration_manifest.json`, its JSONL template, and zero-provider
replay outputs. The manifest is intentionally still pending; completing it
requires human decisions and is not part of an automatic test run.
