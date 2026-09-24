# Small manual inspection example pack

User requests a few diverse samples covering current app features, with images and prompts in `example/`, for the user to test manually.

Deliver six existing real source images covering PL/BX/FS/AJ/BLOCK plus an uncertain, five-class-empty OTHER scene. Reuse them for optional mode comparison, linked enhancement, pixel-ROI geometry, review/report and saved-result comparison. Include Chinese copyable prompts, accurate UI prerequisites, sources and an empty result worksheet. Provide a portable ZIP for local browser uploads. Do not invoke inference, upload examples into the running app, submit human reviews, change source pixels/labels or change application code.

Verify image decoding/dimensions and byte equality to each source, unique sample IDs/groups, target-label coverage, ROI bounds, local document links and ZIP contents. Have a bounded independent read-only check of prompts against supported workflows. These are curated manual examples with possible training overlap, not a new evaluation set or ground truth for current model predictions.

Workflow: Trellis owns task state; jj change kvlkotxo separates this work from the preceding frontend change. No extra implementation plan or application tests are needed for copied assets and documentation.
