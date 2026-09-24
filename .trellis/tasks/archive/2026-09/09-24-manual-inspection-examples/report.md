# Delivered: manual inspection examples

2026-09-24, jj change `kvlkotxo`, parent frontend change `uwpsyylp`.

- Added `example/` with six source-identical JPEGs, six Chinese prompt guides, overview/interaction matrix, blank result sheet and source metadata. Generated `example/manual-test-pack.zip` (15 files, about 0.95 MB); archive is locally ignored because it duplicates the checked-in sources.
- PL/BX/FS/AJ/BLOCK annotation references plus a five-class-empty, known non-target/OTHER scene. Six distinct source groups. Images cover 640×640, 720×576 and 1920×1080, fine traces, wall/shape ambiguity, branch structure, real dark scene and blur/reflection. Visually inspected all six.
- Reuse the six originals for optional same-image mode comparison, pixel-ROI segmentation/relative statistics, linked adaptive enhancement, evidence/review, saved-result comparison, report and refresh recovery. Prompts specify actual UI selectors and terminal prerequisites; image labels are not prescribed model answers.
- Validated image decoding, dimensions, exact byte equality to local original files, distinct IDs/groups, five-class coverage, geometry ROI bounds, prompt lengths, all local Markdown links and ZIP integrity/content parity. Evidence: `var/evaluations/manual-inspection-examples/validation.json`.
- Independent read-only workflow review completed. Clarified geometry's full-image depth plus ROI statistics, and distinguished the original 36-image manifest provenance from this six-image selection.
- No application code/config changes, service restart, inference, paid requests, task uploads or artificial human reviews. Application test suites were not rerun because this task only copied assets and wrote docs; actual manual runs belong to the user.
- Limitations: curated existing source images may overlap model training/prior experiments; no new benchmark, accuracy claim, independently verified normal sample or video coverage. Existing source OSD/blue masking blocks were preserved.
