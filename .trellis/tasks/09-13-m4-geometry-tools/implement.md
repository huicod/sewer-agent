# T13-A Implementation Plan

Trellis controls state; Superpowers supplies TDD/verification. Approved roadmap and jj flow override repeat gates/worktrees.
Goal: reusable prompted-mask and relative-depth tools with real CCTV outputs.
Architecture: original asset → local adapter → new evidence → same-source pixel/relative summary.
Tech: Python3.12, existing Torch/NumPy/Pillow/MediaStore. Spec: design.md.
Constraints: original image/frame only; ≤4,194,304 pixels; one explicit box; no metric units, online/VLM/detector changes, training, hashes or push.

- [x] Research official weights/licenses/APIs, pick one model per tool in research/models.md; no gated downloads or broad training environment.
- [x] Root owns contracts/geometry.py, vision/geometry.py and tests/test_geometry_tools.py. Test first: an 8×6 image mask [2:4,3:6] gives area=6, image_plane_fraction=.125; wrong-source depth raises; invalid/empty output creates no assets and unloads. Run failing pytest, implement request/result validation, immutable mask/array assets and measurements, then pass.
- [x] Model implementer owns vision/geometry_models.py and tests/test_geometry_models.py. LocalSegmenter(checkpoint,device).segment_image(image,prompt) → MaskPrediction; LocalRelativeDepth(checkpoint,device).predict_depth(image) → H×W float32 with larger_is metadata. Test library-boundary local-only loading, original coordinates, output shape and unload before implementation. Provision only official weights under var/models/geometry; no VCS changes.
- [x] Evaluator implementer owns evaluation/geometry.py and tests/test_geometry_evaluation.py; root integrates and runs GPU. Freeze 4–6 images/boxes before GPU. Test new-directory guard, failure visibility, assets and original preservation with doubles. Run local GPU to var/evaluations/2026-09-13-geometry-tools/verified, inspect actual PNG/JSON; no API.
- [x] One scoped independent review and necessary fixes; `.venv/bin/pytest -q backend/tests --tb=short`, `.venv/bin/ruff check backend scripts`, `git diff --check`, complete diff inspection.
- [x] Save report/task/roadmap/journal and local jj checkpoint. Next T13-B handles dialogue/Temporal/DB/UI; do not claim full T13 complete.
