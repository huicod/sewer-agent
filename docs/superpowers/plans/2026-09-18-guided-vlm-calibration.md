# Guided VLM Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the remaining VLM review calibration work without leaking test-set labels, and produce evidence for or against a 75% target.

**Architecture:** Keep the conservative fusion policy as the safety floor. Add versioned per-class detector protection thresholds, explicitly separate original-candidate presence from coexisting classes, gate additions through a stricter confirmation contract, and create a small human calibration manifest/evaluator. Calibration artifacts are development inputs; the frozen test set remains untouched until final evaluation.

**Tech Stack:** Python 3, Pydantic contracts, pytest, existing Ultralytics detector adapter, existing Qwen-compatible VLM client, TypeScript validators/UI, Trellis task artifacts, jj working copy.

**Spec:** `.trellis/tasks/09-18-vlm-conservative-fusion/prd.md` plus the five approved priorities in the conversation.

## Global Constraints

- Do not tune thresholds or fusion rules on the frozen 606-image test labels.
- Do not fabricate human decisions; calibration records must remain explicitly pending until reviewed.
- Keep `guided-conservative-v1` as the default safety floor and preserve explicit historical replay.
- Do not add YOLOE/OWLv2/Grounding-DINO or another detector to the production path.
- Do not call the VLM for the 606-image replay; any fresh API run must be a separately approved calibration sample.
- Keep old cached reviews readable with an explicit fallback of unknown original presence.

### Task 1: Validation-only class threshold analysis

**Files:**
- Create: `.trellis/tasks/09-18-vlm-calibration/analyze_detector_thresholds.py`
- Create: `.trellis/tasks/09-18-vlm-calibration/README.md`
- Test: `backend/tests/test_guided_calibration.py`

- [ ] Write tests for per-class threshold candidates using validation-only detections and explicit tie-breaking.
- [ ] Run the tests red.
- [ ] Implement a no-API analyzer that consumes existing validation predictions/labels, reports candidate thresholds and counts, and never writes production config.
- [ ] Run tests green and generate a versioned calibration report.

### Task 2: Separate original presence from coexisting labels

**Files:**
- Modify: `backend/src/pipe_inspector/agent/guided_review.py`
- Modify: `backend/src/pipe_inspector/agent/guided_fusion.py`
- Modify: `backend/src/pipe_inspector/contracts/image_evidence.py`
- Modify: `frontend/src/validators.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/ImageEvidencePanel.tsx`
- Test: `backend/tests/test_guided_review.py`, `backend/tests/test_guided_policy.py`, `backend/tests/test_image_evidence.py`, `frontend/src/image-evidence.test.tsx`

- [ ] Add versioned `original_presence` (`present`, `absent`, `uncertain`) with an old-record fallback of `uncertain`.
- [ ] Require the new guided prompt/schema to answer original presence independently from `coexisting_labels`.
- [ ] Persist both fields in fusion/evidence records and display them without replacing the YOLO label.
- [ ] Keep legacy v2/v3 cached records valid and add focused tests.

### Task 3: Stricter addition confirmation

**Files:**
- Modify: `backend/src/pipe_inspector/agent/guided_review.py`
- Modify: `backend/src/pipe_inspector/agent/guided_fusion.py`
- Modify: `backend/src/pipe_inspector/agent/guided_policy.py`
- Test: `backend/tests/test_guided_review.py`, `backend/tests/test_guided_policy.py`, `backend/tests/test_online_guided_review.py`

- [ ] Add an explicit addition-confirmation status to the guided response contract.
- [ ] Require a localized box, clear evidence, no overlap with detector candidates, and confirmation status before an addition can ever be promoted.
- [ ] Keep the default conservative path pending even after model confirmation until calibration evidence authorizes promotion; expose the gate reason.
- [ ] Verify unconfirmed additions never enter automatic predictions.

### Task 4: Human calibration manifest and evaluator

**Files:**
- Create: `backend/src/pipe_inspector/evaluation/guided_calibration.py`
- Create: `backend/tests/test_guided_calibration_manifest.py`
- Create: `.trellis/tasks/09-18-vlm-calibration/calibration_manifest.json`
- Create: `.trellis/tasks/09-18-vlm-calibration/calibration_template.jsonl`

- [ ] Build a stratified, group-disjoint calibration manifest from the validation split only.
- [ ] Include original YOLO score/class, VLM review fields, proposed additions, and empty human decisions; do not infer decisions from model output.
- [ ] Add an evaluator that computes per-class detector threshold summaries and VLM accept/reject/revise/addition precision only after human decisions are present.
- [ ] Report the number of pending human labels and block any claim of calibrated accuracy while pending.

### Task 5: Verification and independent test evaluation

**Files:**
- Modify: `.trellis/tasks/09-18-vlm-calibration/README.md`
- Create: `.trellis/tasks/09-18-vlm-calibration/report.md`

- [ ] Run focused TDD suites, full backend/frontend suites, Ruff, TypeScript build, and diff checks.
- [ ] Re-run cached conservative/legacy replay with zero provider attempts.
- [ ] Only after calibration records are complete, run the frozen 606-image test once and report precision, recall, F1, and per-class results.
- [ ] State explicitly whether the chosen 75% metric is reached; otherwise preserve the evidence and stop without further model stacking.
