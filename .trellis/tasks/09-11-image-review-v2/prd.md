# Sewer2403 image review v2 and paired evaluation

## Goal

Improve existing still-image VLM review using Sewer2403's five-class semantics,
then measure the change on a modest fixed paired sample. User approved this
follow-up on 2026-09-11; video development and SEDD evaluation are paused.

## Requirements

- Describe observable features, lookalikes and uncertainty for all five classes.
- BLOCK remains the training mapping CJ/JG/ZW, not only complete obstruction.
- Keep the checkpoint, detector thresholds, API model and deterministic decision
  policy fixed for an interpretable prompt-only comparison; no new visual models.
- Retain the previous 12 images as development/regression examples. Freeze 36 new
  GT-only selected images before prompt changes; exclude previous source groups.
- Compare raw detector, v1 and v2 on those same 36 images. Keep pending results
  separate from automatic confirmations. Do not replace failed/poor samples.
- Preserve original media, old evaluation outputs and credentials. No video runs,
  training, deployment/service restarts, dependencies or remote pushes.

## Acceptance Criteria

- [x] Outgoing requests carry per-class visual guidance and explicit review rules.
- [x] Existing response validation, full-image auditing and human-review gates pass.
- [x] Fixed new manifest has six samples per target class / target-empty stratum;
      BLOCK includes two each from CJ/JG/ZW. Training independence is not claimed.
- [x] Saved paired results include class-level P/R/F1, FP/FN, pending counts,
      API failures/calls and representative gains/regressions; no unmeasured claims.
- [x] Relevant tests, one scoped review and a concise Trellis handoff are saved.

## Notes

- Bounded change to the existing reviewer, following the approved roadmap.
- Budget: at most 36 v1 + 36 v2 + 12 regression image reviews, one attempt per
  eligible image; skipped reviews do not consume calls. No live retuning loop.
- Label references: local FIVE_CLASS_MAPPING and the defect definitions published
  at https://www.songjiang.gov.cn/shsj_yongfeng/xwzx/001002/20250611/07d843cb-1e9a-4a2a-8752-58f53f53f87c.html .
  These inform visual guidance, not severity measurements or normative compliance.
