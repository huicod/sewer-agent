# Design

Build a validation-only runner by reusing `GuidedImageReviewer`, the existing
OpenAI-compatible client, `fuse_guided`, `score_boxes`, and the saved YOLO
validation prediction export. The runner reconstructs an `ImageObservation`
from each saved detector prediction, calls the VLM once per sample, then
scores raw detector boxes, conservative fused boxes, and an explicitly labeled
experimental view that includes clear, localized VLM additions. The
experimental view is diagnostic only and cannot alter the online default.

Each sample is checkpointed atomically. Output includes state JSON, summary
JSON, request evidence, and a static gallery. Pending human labels remain
unchanged; this run measures model behavior but does not authorize promotion.

A separate validation script can submit each projected VLM-only addition to a
fresh guided request as a synthetic low-confidence candidate. This measures a
real second-call gate without treating the first response's
`confirmation_status` as independent evidence; accepted results remain
diagnostic and pending human review.
