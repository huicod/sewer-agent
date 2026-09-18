# VLM calibration implementation report

## Completed in this iteration

- Added validation-only per-class protection-threshold analysis. The candidate
  thresholds are AJ 0.02, BLOCK 0.17, BX 0.72, FS 0.77, and PL 0.43. These
  values meet a 0.75 precision target on the saved validation export where
  possible, but are not yet promoted as a production accuracy claim.
- Bumped the guided prompt contract to `yolo-guided-review-v4`. Each candidate
  now carries `original_presence` separately from `coexisting_labels`.
  Contradictory combinations fail closed in both the provider adapter and the
  online decision adapter.
- Added `confirmation_status` for additional anomalies. Conservative fusion
  requires `confirmed`, a valid localized box, clear evidence, and no duplicate
  overlap before it can even be considered; the default path still keeps the
  addition pending for human review.
- Added a deterministic, group-disjoint 60-image human calibration manifest
  from the validation split. All 60 records remain `pending`; no human labels
  or VLM calls were fabricated.
- Persisted and rendered the new fields in image-evidence JSON, HTML, and the
  frontend without replacing the original detector label or score.

## Verification

- Backend: `1175 passed, 159 skipped`.
- Frontend: `123 passed`; production TypeScript/Vite build passed.
- Ruff and `git diff --check` passed.
- Cached replay used zero provider attempts. On the frozen 606-image comparison
  state, conservative fusion reproduces the detector baseline exactly:
  `TP=501, FP=230, FN=260, precision=0.68536, recall=0.65834, F1=0.67158`.
  Explicit legacy replay remains available and reproduces the old guided result:
  `TP=477, FP=441, FN=284, precision=0.51961, recall=0.62681, F1=0.56820`.

## Accuracy and next gate

The 75% target is not claimed: the frozen test replay F1 is 67.16%, and the
human calibration set is still pending. The next authorized step is to have a
reviewer complete the 60 manifest records, then run one separately recorded
VLM calibration evaluation and only afterward evaluate the frozen test set.

Artifacts:

- `var/evaluations/2026-09-18-vlm-calibration/thresholds.json`
- `var/evaluations/2026-09-18-vlm-calibration/calibration_manifest.json`
- `var/evaluations/2026-09-18-vlm-calibration/calibration_template.jsonl`
- `var/evaluations/2026-09-18-vlm-calibration/replay/`
