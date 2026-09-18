# Calibrate guided VLM review priorities

## Goal

Implement per-class threshold analysis, explicit original presence, strict addition confirmation, and a human calibration manifest without test leakage.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
# Guided VLM calibration priorities

## Goal

Implement the remaining four approved accuracy-improvement priorities without
using the frozen test set for tuning:

1. per-class detector protection thresholds;
2. separate original-candidate presence from coexisting classes;
3. stricter confirmation for VLM-only additions;
4. a small, explicitly human-labeled calibration set.

The existing conservative fusion policy remains the safety floor.

## Constraints

- Validation data may be used for threshold analysis; the 606-image test split
  is reserved for final reporting.
- Human decisions must be entered explicitly; model output cannot stand in for
  calibration labels.
- Existing cached guided v2/v3 records remain readable with unknown presence
  fallback.
- No additional detector, training run, or bulk VLM replay is part of this task.

## Acceptance criteria

- A no-API validation analyzer emits per-class threshold candidates and counts.
- Guided review schema records `original_presence` independently of
  `coexisting_labels`.
- An addition without explicit confirmation, a valid localized box, and clear
  evidence remains pending and absent from automatic predictions.
- A calibration manifest is stratified/group-disjoint and contains empty human
  decision fields until manually reviewed.
- Focused and full tests pass, and the report states whether 75% is reached
  without claiming calibration while labels remain pending.
