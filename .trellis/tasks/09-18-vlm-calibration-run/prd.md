# Run VLM calibration experiment on validation sample

## Goal

Measure the upgraded guided VLM prompt on the existing 60-image,
group-disjoint validation manifest, then compare detector baseline, guided
candidate review, and VLM-only additions without changing production policy or
tuning the frozen 606-image test set.

## Requirements

- Use the configured Qwen VLM API only for the 60 validation samples.
- Checkpoint every sample and save credential-free request/response evidence.
- Do not add a detector, retrain weights, or change frozen test membership.
- Treat dataset labels as validation references, not human calibration truth.
- Keep additions pending in production; any promotion result is offline only.

## Acceptance Criteria

- [ ] Run is resumable and records provider attempts, errors, prompt/fusion
      pins, raw boxes, VLM review, and experimental addition matches.
- [ ] Report micro and per-class precision, recall, F1, recovered raw misses,
      and added false positives for the validation cohort.
- [ ] Produce a gallery with original, YOLO, VLM suggestions, and ground truth.
- [ ] Persist no credential or authorization header.
- [ ] If precision and recall do not both improve, retain conservative fusion.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
