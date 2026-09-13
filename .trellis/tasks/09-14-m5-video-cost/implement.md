# T15-D Implementation Plan

> Use superpowers:subagent-driven-development, with Trellis as the only task state and one combined task review under project overrides.

**Goal:** Repeatable, bounded live video resource and usage evidence.
**Architecture:** Existing Temporal workflow with test-only measurement; no business logic changes.
**Tech stack:** Python, pytest, local PostgreSQL/Temporal/FFmpeg/GPU, Qwen API.
**Spec:** [design.md](design.md).

## Global constraints

- Preserve accepted detector/VLM policies, model defaults and human gate; open detectors stay retired.
- No extra dependencies, new scheduler, hashes, production writes or paid calls in ordinary tests.
- Only root changes jj/task state and runs paid/GPU experiments; implementation agent writes isolated test files only.

## Task 1 — Video acceptance harness

Files: create `backend/tests/video_cost_live.py` (test helper), `backend/tests/test_video_cost_measurements.py` (offline tests), `backend/tests/integration/test_video_cost_live.py` (opt-in real workflow). Use distinct test basenames for the existing pytest import mode. Reuse `backend/tests/browser_live.py` without copying its recorder or changing it unless a demonstrated compatibility issue is reported first.

- [x] Write failing offline tests for explicit gate/source/output admission and summary behavior (known tokens, missing usage, stage times distinct from total time). Run `.venv/bin/python -m pytest backend/tests/test_video_cost_measurements.py -q`; record expected RED.
- [x] Implement minimal helpers and opt-in test using the design. Test actual helper behavior, not source-text assertions. Ordinary tests must not load GPU or make network/provider calls.
- [x] Run new tests plus `backend/tests/test_browser_live.py`, `backend/tests/test_video_policy.py`; run Ruff on new files. Implementer reports commands and evidence in `implementation-report.md`.

## Task 2 — Root acceptance and handoff

- [x] Inspect no active user GPU work; freeze exact input(s) before new inference. Run first full 22.401953 s recording, API cap 8; only add fixed 30 s second-source clip if needed to cover candidate/event-rich load.
- [x] Inspect actual outputs/frames, provider usage and current official prices if quoting currency. Save a compact measured table and artifact links in `report.md`; do not guess absent billing or precision.
- [x] One independent scope/spec/code/evidence review; focused fixes only. Run full backend/Ruff once if new code lands; frontend need not rebuild for test-only additions.
- [x] Update roadmap, session journal and task status. Save jj checkpoint, no push; leave live app operational.

Preflight: helper/test files are isolated from root documentation/sample preparation; existing recorder supplies API budget and GPU measurements, production workflow supplies all decisions. No competing state machine or conflicting task outputs.
