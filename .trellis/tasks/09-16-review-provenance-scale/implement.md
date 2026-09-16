# Execution checklist

- [x] Add failing API/report tests for original FS score versus revised PL; implement stored-evidence projection and shared report output. Check absent/legacy/supplement evidence and HTML escaping.
- [x] Add failing frontend evidence-panel tests; implement on-demand original/VLM/final display and original-pixel overlays; run frontend test/build.
- [x] Add failing explicit-frame grouping and tile-coordinate/matching tests; fix grouping and run bounded dataset diagnostics, preserving historical evidence.
- [x] Update current README/environment/runbook/architecture descriptions; save model-decision evidence and progress without duplicating roadmap.
- [x] Run relevant/full affordable regression, inspect full diff, one independent scoped review; save Trellis report/journal and jj checkpoint. Do not train, restart services or push without new authorization.

Validation: `.venv/bin/python -m pytest backend/tests -q`, `.venv/bin/ruff check backend`, `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend test`, frontend build, targeted data-prep/diagnostic tests and cached parity replay.

Write isolation: main owns backend/docs/task state; frontend worker owns frontend only; data worker owns `yolov26/code/prepare_sewer2403_yolo.py`, corresponding tests, task-local diagnostic script/tests/evidence note and ignored evaluation output. Only main uses jj.
