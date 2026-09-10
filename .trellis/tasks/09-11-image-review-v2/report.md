# Sewer2403 image review v2 — paired evaluation

## Scope and implementation

User alignment (2026-09-11): concentrate on still images from Sewer2403; pause
video work and further SEDD testing. SEDD is not treated as this model's primary
acceptance set. Keep the existing five-class YOLO checkpoint, Qwen API model,
thresholds, review triggers and deterministic decision policy unchanged.

`backend/src/pipe_inspector/agent/image_review.py` now sends `image-review-v2`:
Chinese review instructions plus `label_guidance` for all five classes, including
when the detector returns no candidates. Each class has observable features and
lookalikes; BLOCK explicitly maps CJ (sediment), JG (encrustation), ZW (obstacles),
without requiring complete blockage. Guidance distinguishes material loss (FS)
from attached deposits, fine fractures (PL) from seams, shape deformation (BX)
from perspective, and lateral connection (AJ) from the main pipe opening.

Names/mapping follow `yolov26/code/prepare_sewer2403_yolo.py`; qualitative definitions
were checked against the [published defect tables](https://www.songjiang.gov.cn/shsj_yongfeng/xwzx/001002/20250611/07d843cb-1e9a-4a2a-8752-58f53f53f87c.html).
Lookalike/uncertainty instructions are task-specific review guidance, not claims of
physical measurement, cause, severity or normative compliance.

The prompt asks for visible evidence when rejecting/revising, uncertainty rather
than rejection for unreadable regions, separate full-image coverage, no duplicate
additions and no OSD/known inspection equipment as defects. These are **instructions
to the model, not deterministic semantic enforcement**. Existing candidate indices,
JSON schema and original-pixel bounds remain validated. The original full image and
numeric candidate boxes are still sent; no box overlays/crops or new vision models.
The shared image reviewer is also consumed by video, but no video implementation,
video run, service restart or deployment was performed in this task.

## Fixed data and comparison

Artifacts: `var/evaluations/2026-09-11/` (ignored generated/local data).

- `sewer2403-paired-manifest.json`: 36 new GT-only selected images, seed 20260911;
  six each PL/BX/FS/AJ/BLOCK/target-empty OTHER. BLOCK contains two each CJ/JG/ZW.
  All filename-derived groups are unique and exclude the prior 12 pilot groups.
- Previous 12 images remain development/regression examples, reported separately.
- Selection happened before prompt edits. No prediction-based replacement or failed
  case reruns. The source contains augmented images too; this is not an independent
  original-camera benchmark. Lost training splits mean possible training overlap.
  Target-empty means annotated other classes, **not verified normal**.
- `paired-v1/state.json`: unchanged v1 process, started before code edits.
- `paired-v2/state.json`, `regression-v2/state.json`: v2 uses exact cached detector
  and quality observations from the corresponding v1 states; no second GPU pass.
  Credentials and ground truth are never included in review messages.
- `paired-comparison.json`, `regression-comparison.json`: offline class-aware IoU
  0.5 scoring, complete-set totals, per-class scores and per-image comparisons.
  `review_calls` is counted from all saved records. `summary.json.this_invocation`
  timing/calls describe only that process invocation, not cumulative resume timing.
- Helpers `build_manifest.py`, `review_cached.py`, `compare.py` reproduce selection,
  exact-observation review and offline analysis respectively. Each v2 output also
  saves the actual `prompt.json`; v1 source is in baseline commit `281e2e13`.

## Verification

TDD: the new outgoing-request test first failed because `label_guidance` was absent,
then passed after implementation. Existing image bytes/schema/index/bounds checks
remain covered. Focused image review/policy/evaluation: 43 passed. Full offline
backend regression: 446 passed, 75 opt-in integration tests skipped. Ruff and
`git diff --check` passed. No frontend or storage changes require new UI/migration work.

Independent read-only review found no blocking issue in the production diff and
independently checked manifest counts/group exclusion/BLOCK subtypes. A concern
about invocation-local counters was resolved by the comparison script's cumulative
record counts and explicit timing labels; they are not reported as total API counts.

## Completed paired results

All 36 new images completed in both arms; 34 annotated five-class boxes. Each arm
made 31 conditional calls (five skips). V1 had one `response_schema_invalid` on
`testmosaicmix-54-_jpg.rf.2c71502464a68b8beacb70529aa5cdf3`; v2 had zero failures.
No failures were retried. V2 reviewed the new set in 244.48 s using cached YOLO/quality
observations (not end-to-end detector latency). The separate 12-image regression made
nine v2 calls in 83.91 s, zero failures. **This task: 71 real API calls total**
(31 v1 + 31 v2 + 9 regression). Original 12-image v1 responses were reused.

| New 36 images / output | TP / FP / FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- |
| Raw YOLO | 29 / 8 / 5 | 0.784 | 0.853 | 0.817 |
| V1 automatic confirmations | 12 / 1 / 22 | 0.923 | 0.353 | 0.511 |
| V2 automatic confirmations | 19 / 2 / 15 | 0.905 | 0.559 | 0.691 |
| V1 queue including pending | 20 / 28 / 14 | 0.417 | 0.588 | 0.488 |
| V2 queue including pending | 24 / 28 / 10 | 0.462 | 0.706 | 0.558 |

Automatic-confirmation per-class F1 (v1 -> v2): PL .364 -> .800, FS .000 -> .500,
BX .500 -> .667, AJ .833 -> .833, BLOCK .600 -> .545. Full class TP/FP/FN are in
`paired-comparison.json`. Pending findings declined 44 -> 34, but images requiring
review rose 23 -> 25 (more explicit incomplete/uncertain coverage); these are not
equivalent workload measures.

The old API failure affected a four-PL-box image and explains four of the seven
additional auto-confirmed true positives. On the **diagnostic 35-image subset with
no error in either arm**, automatic F1 still rises .558 -> .638 (12 -> 15 TP;
1 -> 2 FP), and queue F1 .438 -> .513. This does not replace the full-set headline.

On the prior 12 development/regression images, automatic F1 .667 -> .737
(6/0/6 -> 7/0/5), queue F1 .483 -> .606 (7/10/5 -> 10/11/2). Their raw YOLO F1
remains .846. These known cases are not used as independent confirmation evidence.

**Conclusion:** v2 improves over v1 in this fixed exploratory comparison, including
the no-error subset, but both automatic and queue views remain below raw YOLO's F1
and recall. BLOCK did not improve overall, added suggestions remain noisy, and
one stochastic call per image does not establish statistical significance. This is
not independent training-held-out performance, mAP or true-normal accuracy.

## Representative evidence and remaining problems

Images below are under
`var/datasets/sewer2403_source/extracted/市政下水雨污管道缺陷数据2403/images/`.
Exact boxes and complete model responses are in comparison rows / original states.

- `f71029ab1dfc22049a4a8ea1222f3876.jpg`: GT FS, YOLO correct. V1 rejected the
  region as glare; v2 describes local surface roughness/pitting and accepts FS.
  A true positive is restored **relative to the supplied annotation**.
- `c772fc059a1a163bdfd9321a3d558a17.jpg`: GT BX, YOLO correct. V1 changed it to
  BLOCK because of deposits; v2 describes compressed/buckled upper corrugations
  and accepts BX. Its additional BLOCK suggestion is still an unmatched pending FP.
- `19-04241-mp4_000157-315_jpg.rf.85ae331aeca312896e3dc307821d9098.jpg`:
  v2 accepts two overlapping BLOCK candidates where v1 rejected one as duplicate.
  Scoring yields one additional confirmed FP. Localized evidence association is
  needed; class agreement alone does not establish distinct defect count.
- Prior `CL_001060_png_jpg.rf.1708451e91bb980747df0c391b950ea7.jpg`: v1 rejected
  a GT-matched faint PL; v2 abstains and retains it for review, **not auto-confirmed**.
- Prior `16-14063R-143_jpg.rf.caadc905265c4650c0fc1ecafd8d2d0f.jpg`: v2's newly
  accepted BLOCK matches GT, but the explanation still relies on high water and
  contradicts the prompt's evidence requirement. Metric improvement is not proof
  of sound domain reasoning. Similarly, model duplicate handling is inconsistent
  across examples despite the explicit instructions; prompts are not hard gates.

No further live tuning was performed on these results. Keep model evidence and
human-review status separate. Next image work should address localized evidence /
duplicate matching and expert adjudication of FS/BLOCK/uncertain cases before
claiming a detector accuracy upgrade. Video/T09 remain paused per the latest user
direction. No new API credentials are needed.
