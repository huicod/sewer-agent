# Paired review pilot evaluator

## Current evidence boundary

Superseded by the user-authorized 2026-09-10 exploratory run: 30 external SEDD images
and 12 possibly training-overlapping Sewer2403 examples, 39 actual conditional Qwen calls.
No overall accuracy gain was established. See [data and result report](../09-10-m2-video-workflow/data-report.md)
for fixed manifests, SEDD's limited PL/BLOCK annotation scope and paired metrics.
The original independent full-five-class split remains unrecoverable; this pilot does not replace it.

Sewer2403 labels made empty by the five-class mapping mean only “none of PL/BX/FS/BLOCK/AJ.”
They can contain other annotated defects and are not a true-normal set. The evaluator therefore
reports no true-normal accuracy and no mAP.

## Interface

`scripts/review_eval.py` accepts one operator-supplied, GT-only sampled manifest. The manifest is
fixed and saved verbatim on first live use; resume requires the same manifest and nonsecret
detector/VLM pins. Sample IDs and group IDs must be unique, paths absolute, boxes valid in original
image pixels, and sample count at most `--max-samples` (48 by default).

Every manifest sample must have a saved record before comparisons are emitted. Summary output always
states the expected, evaluated, and missing sample IDs and counts. An interrupted or empty run is
`incomplete`, contains only this coverage/status information, and exits nonzero; it cannot present
processed-subset metrics as a fixed-set comparison.

Minimal manifest example (illustrative only; it is not a selected sample):

```json
{
  "dataset_name": "operator-supplied-held-out-pilot",
  "split_provenance": "Describe the factual source and prior-use history here.",
  "samples": [
    {
      "sample_id": "fixed-001",
      "group_id": "source-video-or-capture-001",
      "image_path": "/absolute/path/to/fixed-001.jpg",
      "width": 1280,
      "height": 720,
      "ground_truth": [
        {"label": "PL", "bbox_xyxy": [100, 120, 310, 360]}
      ]
    }
  ]
}
```

Allowed labels are `PL`, `BX`, `FS`, `BLOCK`, and `AJ`. An empty `ground_truth` list means no
target-five box, not normal.

Summary-only mode never invokes the detector or provider and requires saved state:

```bash
.venv/bin/python scripts/review_eval.py \
  --manifest /absolute/path/to/fixed-manifest.json \
  --output /absolute/path/to/existing-evaluation-output
```

The root/operator may explicitly enable the bounded live path only after confirming provenance and
configuration:

```bash
.venv/bin/python scripts/review_eval.py \
  --manifest /absolute/path/to/fixed-manifest.json \
  --output /absolute/path/to/evaluation-output \
  --max-samples 48 \
  --live
```

Live operation is fixed at detector confidence 0.25, image size 960, inherited NMS IoU 0.7, and
class-aware evaluation match IoU 0.5. It reuses the production quality gate, conditional
`ImageReviewer`, prompt/policy, and `decide_image`. It is sequential and makes at most one review
call for each uncached eligible image. Saved failures are not retried and remain in metric
denominators. A crash after provider success but before the local checkpoint can still duplicate a
billable request on resume.

A local detector, dimension, image-quality, or file failure stops the live command and leaves the run
incomplete. Successfully checkpointed earlier samples remain cached and are not rerun on resume, but
no comparative metrics are reported until every fixed sample has a record. Provider review failures
are different: they are saved as safe error codes, retain the production-policy output, and remain
in the denominator of a complete run.

Resume pins include detector ID, label-space/model/checkpoint and operating point; image quality
policy version; VLM endpoint/model/prompt/review policy; thinking mode, output-token cap, timeout,
maximum image bytes; and the strong-evidence decision threshold governing rejected detections.
Changing any pin refuses resume before detector or provider calls.

## Reported metrics

The summary recomputes from local state without API access and reports, for the same images:

- raw detector boxes;
- retained localized five-class queue entries, including pending items;
- auto-confirmed localized findings only;
- per-class and micro TP/FP/FN, precision, recall, F1, and FP/image;
- image-level five-label-set exact match, paired improved/worsened/unchanged counts, and deltas;
- pending, abstain/need-more-context, review-failure, and nonlocalized-suggestion counts.

VLM revisions and additional findings remain pending under the actual production policy. The
separate suggestion-inclusive label-set number does not silently promote them to confirmed boxes.
Precision changes caused only by deferral must be interpreted together with recall and pending
review workload.

## Verification

The scoped tests use hand-computed boxes and deterministic fakes. They cover class-aware greedy
matching, duplicate/wrong-class/missed boxes, cross-image isolation, manifest bounds and identity,
queue/confirmed separation, persisted review failures, resume pin mismatches, API-call skipping,
incomplete-run suppression/nonzero CLI behavior, local-failure resume, full live pin coverage, and
read-only summary behavior. No GPU, provider, `.env`, or dataset split was used.
