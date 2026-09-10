# Recall-first image fusion and independent VLM discovery

## Goal

Protect detector recall, add independent full-image/tiled VLM audit, test fixed Sewer2403 data; video paused.

## Requirements

- Preserve every original YOLO label/box in actionable output; VLM rejection or
  revision cannot erase it. Disagreement/additions remain pending, not confirmed.
- Independently audit all images, including strong detections, without detector
  predictions in the API request. Reuse v2 class guidance; full image plus up to
  four native-resolution overlapping crops for images larger than 1024 pixels.
- Use one bounded API call per image, validate view-local coordinates and map to
  original pixels. Keep original media and old evaluation results immutable.
- Integrate recall policy into image jobs and supplements, with authoritative
  mode/policy/prompt/view pins. Keep candidate review as default; independent
  discovery remains selectable but experimental after observed FP regression.
  Video keeps its existing candidate-review semantics.
- Test fixed 36 images plus 12 regression images using cached YOLO/quality inputs;
  at most two full paired arms (96 paid calls), no failed-case retries or sample replacement. No training,
  SEDD, video runs, dependencies, service restarts or remote pushes.
- Report candidate P/R/F1, preserved/lost TP, actual added matches, FP and pending
  counts; automatic confirmations remain a separate view, not a recall substitute.

## Acceptance Criteria

- [x] Tests prove originals survive reject/revise/abstain/error outcomes.
- [x] Independent requests contain no detector hints; view count/pixels/coordinate
      mapping and invalid response handling are tested.
- [x] Image workflow audits strong detections too and respects pinned versions,
      checkpoint/retry boundaries; paused video semantics remain unchanged.
- [x] Fixed real evaluation and scoped review distinguish preservation from true
      recovered misses. Save regression results, progress and remaining limitations.

## Notes

- First independent arm exposed coordinate-protocol failures; Qwen official docs
  specify normalized coordinates. Correct the explicit protocol and rerun the same
  48 images once; retain the first arm, including failures. No per-case retries.
- Result: 36-image candidate recall +1/34 targets, but substantial FP/confirmation
  regression and no target-level recovery on empty-detector images. Do not promote
  the experimental reviewer as default; this iteration is not overall accuracy acceptance.
