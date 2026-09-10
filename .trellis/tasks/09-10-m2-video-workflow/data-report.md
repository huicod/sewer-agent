# Fixed paired-pilot data report

## Extracted sources

- `var/datasets/sedd_external/image`: 7,563 SEDD JPG stills.
- `var/datasets/sedd_external/bbox`: 2,562 Pascal/VOC XML files. The 11,987 AVI
  members in the archive were not extracted by this data task.
- `var/datasets/sewer2403_source/extracted/市政下水雨污管道缺陷数据2403`:
  19,049 image/XML pairs from the supplied Sewer2403 multipart archive.

SEDD XML is box-localized and contains only `crack`, `deposit`, and `root`. Object
counts are 2,252 / 698 / 1,866; annotated-image counts are 1,536 / 564 / 882.
For this evaluation, `crack -> PL`, `deposit -> BLOCK`, and `root` stays
outside the five-class label space. SEDD metrics are restricted to PL/BLOCK: the
archive does not establish exhaustive annotations for AJ/BX/FS, so predictions of
those labels remain unscored, not false positives. A root-only image has empty
PL/BLOCK ground truth and is known OTHER, not a normal image. The 5,001 JPGs without XML are not
used because the archive contains no README defining their label completeness.

SEDD has a metadata defect: 924 of 2,562 XML internal `<filename>` values differ from
the XML stem, the internal names are not one-to-one, and 381 mismatches reference an
image whose dimensions disagree with the XML. The fixed sample consequently admits
only XMLs whose stem equals the internal filename, whose same-stem image dimensions
match, and whose boxes are in bounds. Eligible pure strata are PL 475, BLOCK 331, and
root-only 522. The archive provides no still-image split or source/video group IDs.

## Fixed manifests

- `var/evaluations/2026-09-10/sedd-manifest.json`: 30 external images selected with
  seed 20260910, comprising 10 PL-only, 10 BLOCK-only, and 10 root-only known-OTHER
  images. They contain 16 PL boxes and 14 BLOCK boxes. The per-row `unknown-source-*`
  group IDs are identities only; pipe/video group independence cannot be claimed.
- `var/evaluations/2026-09-10/sewer2403-manifest.json`: 12 source-domain illustrative
  images selected with the same fixed RNG stream, two each for PL, BX, FS, BLOCK, AJ,
  and annotated five-class-empty OTHER. Existing `group_key(..., "video")` values are
  unique within the manifest. The lost original split means possible training overlap;
  this manifest is not held-out evidence.

Selection used original GT only and did not run or inspect detector/VLM predictions.
Both manifests validate against `EvaluationManifest`, contain unique sample/group IDs,
use existing image dimensions, and are within the evaluator's 48-image limit.

## Paired results (2026-09-10)

Fixed checkpoint, confidence 0.25, image size 960, matching IoU 0.5 and existing
`image-review-v1`/decision policy; Qwen `qwen3.8-max`. All 42 images completed,
39 conditional API calls (30 external + 9 source-domain), zero review failures.
No sample replacement, retraining, threshold/prompt tuning, or failed-case reruns.

| Data / output view | TP / FP / FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- |
| SEDD PL/BLOCK, detector | 0 / 1 / 30 | 0.000 | 0.000 | 0.000 |
| SEDD PL/BLOCK, retained queue including pending | 2 / 5 / 28 | 0.286 | 0.067 | 0.108 |
| SEDD PL/BLOCK, automatic confirmations | 0 / 1 / 30 | 0.000 | 0.000 | 0.000 |
| Sewer2403 five classes, detector | 11 / 3 / 1 | 0.786 | 0.917 | 0.846 |
| Sewer2403, retained queue including pending | 7 / 10 / 5 | 0.412 | 0.583 | 0.483 |
| Sewer2403, automatic confirmations | 6 / 0 / 6 | 1.000 | 0.500 | 0.667 |

**No overall accuracy gain established.** Source-domain automatic confirmations remove
3 false positives but also lose 5 true positives from that output view; lower recall
outweighs precision improvement. SEDD adds 2 localized PL candidates only to the
pending queue, not to confirmed results. There are 34 / 17 pending findings respectively.
SEDD also has 18 predicted FS boxes, deliberately unscored because FS annotations are
not established. These are small exploratory pilots, not five-class external benchmark,
true-normal accuracy, mAP, or statistically established generalization evidence.

Saved original observations/reviews/decisions/pins:
`var/evaluations/2026-09-10/{sedd,sewer2403}/state.json`.
Offline `summarize_pilot.py` generates `scoped-summary.json` from those same records;
SEDD uses existing per-class PL/BLOCK counts and leaves raw five-class output intact.
Next accuracy work needs expert audit of disagreements/annotation semantics and a
fixed full-five-class held-out set. Keep the current architecture, but do not advertise
the uncalibrated VLM gate as an accuracy upgrade or silently treat advice as ground truth.

## Three SEDD samples for visual annotation review

| Sample | Original image | Source annotation | Five-class GT |
| --- | --- | --- | --- |
| `sedd-06972` | `var/datasets/sedd_external/image/06972.jpg` | crack `[961, 30, 1091, 292]` | PL, same box |
| `sedd-06796` | `var/datasets/sedd_external/image/06796.jpg` | deposit `[813, 919, 922, 990]`; `[700, 947, 826, 1039]` | BLOCK, both boxes |
| `sedd-05597` | `var/datasets/sedd_external/image/05597.jpg` | root `[597, 24, 652, 237]`; `[1001, 11, 1086, 165]`; `[1301, 16, 1352, 150]`; `[786, 6, 801, 48]`; `[1401, 6, 1429, 103]`; `[674, 4, 705, 85]` | empty (known OTHER, not normal) |

All three are 1,920 x 1,080, and their XML stem, internal filename, and image stem agree.
