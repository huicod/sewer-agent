# T12-B Conversation Routing Report

## Scope

- Added explicit `ENHANCE` admission for new inspections and linked supplements.
- Kept legacy verify-only supplements unchanged, including omitting the
  `enhancement` keyword entirely when enhancement was not requested.
- Added focused conversation and intent tests; no service, API, GPU, dependency,
  or VCS operations were used.

## Behavior

- New inspections compile and persist the pinned plan, then pass that plan to
  `InspectionStarter.start`.
- Enhancement options, original-image eligibility, ROI constraints, size limits,
  and local checkpoint availability are preflighted before task creation.
- Missing enhancement kind, missing super-resolution ROI, video/derived input,
  invalid bounds/size, or absent weights returns a clear non-executing response.
- Linked enhancement reuses the parent's input and frozen evidence, passes only
  the selected `EnhancementOptions` to `admit_supplement`, optionally includes
  verification, and does not mutate the parent task.
- Asset substitution and unsupported supplement actions remain rejected before
  admission.

## Retry recovery fix

- Enhancement retries first perform a read-only request-ID lookup. An existing
  task is reused only when request, session, input asset, instruction, and parent
  identity exactly match; mismatches still raise `SubmissionConflict`.
- Existing inspect tasks bypass mutable model-file preflight and resume through
  `start` with their pinned plan. Existing supplement children return before
  detector evidence/config reconstruction, preserving the admitted run.
- Truly new requests still perform all media, ROI, size, and model-file checks
  before storage admission.
- Terminal retries now state that the existing task/result was reused and do not
  claim that results are unfinished.

## Test evidence

- RED: focused owned suite produced `7 failed, 47 passed` before dispatcher
  support was added.
- Recovery RED: `8 failed, 7 passed` before lookup-first recovery; terminal-copy
  wording then failed independently as `2 failed, 7 deselected` before correction.
- GREEN: final owned-suite rerun produced `54 passed in 0.71s`.
- Regression slice including open-scan wiring produced `65 passed in 2.60s`.
- Final recovery regression slice produced `80 passed in 2.73s`.
- Ruff over all owned Python files: `All checks passed!`.
- Root-provided PostgreSQL replay evidence after removing the dummy checkpoint:
  `1 passed, 2 deselected in 2.83s`; the same child and frozen run were returned.

The full cross-agent backend/integration suite remains root-owned because other
T12-B files were changing concurrently.
