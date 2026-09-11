# Confidence-aware guided review and fixed-image visual validation

## Goal

Respect strong detector evidence during the existing experimental image review, while
retaining low-confidence correction and whole-image missed-defect discovery. Validate
on the same 36 Sewer2403 images plus 12 regression images and export concrete overlays.

## Requirements

- Reuse the existing 0.85 threshold: scores >= 0.85 keep their original label, box
  and score even when VLM disagrees; disagreements remain visible and pending review.
- Explain this detector prior in the versioned prompt and per-candidate metadata;
  do not require the VLM to invent supporting evidence. Lower-score candidates may
  still be rejected/relabelled with clear visual evidence; uncertain ones remain.
- Keep original/full-image plus native contextual crops, class guidance, addition
  gates, detector observations, sample selection and source annotations unchanged.
- User-reviewed cases: main #7/#11 BX and #22 AJ must not be automatically erased;
  low-score FS removal in #17/#18 is acceptable. This is not full ground-truth revision.
- First replay the previous responses under the new fusion (no API), then run one
  new-prompt call per fixed image, at most 48 calls, no retry. Preserve previous runs.
- Report original-annotation metrics separately from these manual case checks.
  Unmatched predictions are not automatically verified false alarms; additional
  unlabelled defects remain possible but are not all assumed correct.
- Export original, YOLO, VLM additions, fused, GT, request views and JSON responses.
- Scope: existing experimental adapter/evaluator only; no production-default switch,
  video, SEDD, training, new dependencies, credential changes, services or remote push.

## Acceptance Criteria

- [x] Regression tests prove protected/low-score behavior, exact boundary and metadata.
- [x] Old-response replay isolates fusion effects; fresh 48-image results are retained.
- [x] Inspect the five named cases and provide navigable per-image PNG/HTML evidence.
- [x] Focused independent review, relevant tests and concise progress/report checkpoint.

## Notes

Bounded follow-up to the approved guided-review design; PRD-only. Do not optimize
thresholds against this fixed set or claim generalization/true precision from incomplete labels.
