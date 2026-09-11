# T11 integrated code review

Reviewed the working-copy diff from `5f574be4` against `prd.md`, `design.md`, and
`implement.md`, including the completed `verified/` artifacts. Review mode was read-only except
for this report.

## Critical

None.

## Important

1. **The CLI can call an incomplete or substituted cohort “complete.”**
   [`backend/src/pipe_inspector/evaluation/incremental.py:327`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/incremental.py:327)
   makes both fixed-cache inputs optional, `validate_samples` accepts any 1–56 samples, and
   [`incremental.py:359`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/incremental.py:359)
   sets `expected_count` to the number that happened to be supplied. Omitting one guided cache or
   the video cache therefore produces `complete: true` after processing that smaller list. The
   video loader also ignores `provenance.schema_version`, while `load_guided_samples` never checks
   that the cache-declared fusion pin is `guided-confidence-v2`; it merely relabels a reproducible
   record with the current policy. This contradicts the fixed 36+12+8 acceptance anchor and can
   silently evaluate a different experiment. Keep the reusable runner generic if useful, but make
   this fixed CLI validate exactly 48 image and 8 video IDs/cohorts, the accepted cache schema and
   policy pins, and report coverage against the fixed 56 rather than `len(samples)`.

2. **The saved snapshot does not pin the pixels or actual model assets it claims to pin.**
   [`backend/src/pipe_inspector/evaluation/incremental.py:242`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/incremental.py:242)
   records caller-supplied configuration plus three detector strings, and
   [`incremental.py:302`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/incremental.py:302)
   checks a source only for decodability and dimensions. On resume, already-recorded sources are
   not opened at all. Replacing an image with different same-sized pixels, or replacing the
   checkpoint/text encoder at the same paths, is therefore accepted; the open result can then be
   compared with baseline predictions from different pixels/assets. In particular,
   [`backend/src/pipe_inspector/vision/open_detector.py:64`](/root/autodl-tmp/server/backend/src/pipe_inspector/vision/open_detector.py:64)
   defines `model_version` as only the checkpoint basename, and the runner does not verify that
   detector `confidence`/`image_size` match the separately supplied configuration. This makes the
   “input/version/config complete and changed input rejected” claim in
   [`report.md:8`](/root/autodl-tmp/server/.trellis/tasks/09-11-m3-incremental-evaluation/report.md:8)
   too broad. A bounded no-hash fix is to retain exact source copies in the run and byte-compare
   them on reuse, require canonical model asset IDs/versions (or immutable checkpoint paths), and
   derive/validate runtime parameters from the detector rather than trusting a parallel dict.

3. **The human-review CSV has neither stable finding identity nor usable source provenance.**
   The durable record already contains stable `open_findings[].finding_id` values at
   [`backend/src/pipe_inspector/evaluation/incremental.py:215`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/incremental.py:215),
   but [`backend/src/pipe_inspector/evaluation/incremental_metrics.py:240`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/incremental_metrics.py:240)
   exports only a per-sample ordinal and candidate fields. The resulting real CSV has no finding
   ID, group/source path or asset version, video frame/timestamp, or gallery/evidence link, so a
   reviewer cannot independently locate an item and later reconcile a decision without joining
   undocumented ordering against `state.json`. Export the aligned finding ID plus the minimal
   source locator and a relative gallery/evidence link, and validate the one-to-one alignment of
   `open_added` and `open_findings`. This stays an offline CSV change; no production human-review
   workflow or truth import is needed.

## Minor

1. **Gallery source availability and MIME are trusted from a filename suffix.**
   [`backend/src/pipe_inspector/evaluation/incremental_gallery.py:186`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/incremental_gallery.py:186)
   skips PIL decoding for recognized suffixes; even an undecodable extensionless file falls back
   to `application/octet-stream` and is returned as available at line 199. A corrupt or mislabeled
   `.jpg`/`.png` can consequently yield broken SVG layers while the index says the original is
   available. Always decode/load once, use the detected format and dimensions for MIME, and emit
   the existing unavailable placeholder on failure.

## Verification and verdict

- The completed artifact currently reports 56/56 records, 32 successful and 24 skipped open scans,
  zero open failures, eight videos with unknown truth, and 13 pending candidates; its metric and
  effect caveats are appropriately explicit.
- Focused unit verification passed: `46 passed` across incremental runner, metrics, gallery, and
  open-detector tests. Those tests do not cover the fixed-cohort omission, same-path pixel/model
  replacement, or CSV reconciliation cases above.
- No production default, prompt, or fusion-policy change was found; the extracted image gate is
  behaviorally equivalent for the production image path, and the extensionless PIL decode fix is
  scoped to the detector adapter.

**Engineering readiness: with fixes.** The real run is useful preserved evidence and the effect
acceptance correctly remains failed, but the fixed-evaluation acceptance gate is not yet reliable
until the three Important provenance/coverage issues are addressed.
