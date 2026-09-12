# T12-D Implementation Plan

Trellis controls state; Superpowers supplies TDD/verification. Approved roadmap task, no second design gate or worktree. Goal: runnable same-input restoration candidate comparison, not online replacement. Spec: `design.md`; Python/PyTorch/Pillow/existing YOLO adapter.

- [x] Research official candidates/weights/licenses and select one per kind. Save `research/models.md`. Do not install broad upstream environments.
- [x] Build a separate lazy `make_restoration_model(model_id, checkpoint, device)` factory and minimal official inference adaptations. First test size, normalization, invalid checkpoint/output, unload and offline-only access; run failing tests then implementation. Files: `vision/restoration_candidates.py`, only necessary `_enhancement_vendor` additions, `test_restoration_candidates.py`.
- [x] Add `evaluation/restoration.py` with explicit manifests and the controls described in design. First test resize/ROI coordinate mapping, bicubic control, visible failures, no overwrite, accurate one-to-one box comparison using local model/detector doubles. Files: module plus `test_restoration_evaluation.py`. Entrypoint: `.venv/bin/python -m pipe_inspector.evaluation.restoration --help`.
- [x] Freeze 6–8 historical Sewer2403 samples and model manifests before inference; download official weights under `var/models/enhancement`, save version provenance, no hashes. Run at most 3 new candidate models and 3 existing models on all samples, local GPU serially, no API.
- [x] Inspect output/box comparisons, record representative detail preservation and failures; no acceptance threshold tuning on this sample set. Run `.venv/bin/ruff check backend scripts` and `.venv/bin/pytest -q backend/tests --tb=short`, one scoped independent review and necessary fixes.
- [x] Save report, task state, roadmap and journal; freeze local jj change, no push. Online behavior stays on `ee158b99` logic regardless of candidate results.
