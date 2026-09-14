# Task 1 backend report

## RED

- The current-turn linked `auto_enhancement` test reached the former rejection reply and created no supplement.
- Auto supplement admission failed with `unexpected keyword argument 'auto_enhancement'`.
- Auto supplement workflow tests observed `verify_image` instead of selection and optional enhancement.
- Linked selection without a child `verify` checkpoint raised `AttributeError`.
- Auto skip/error finalization treated the absent child review as a failed re-review, losing the frozen parent conclusion.

## GREEN

- `ConversationDispatcher` now passes only `auto_enhancement=True` for an authorized linked request; explicit enhancement and geometry paths are unchanged.
- `InspectionStarter.admit_supplement(parent_task_id: UUID, submission: TaskSubmission, *, enhancement=None, geometry=None, auto_enhancement: bool = False)` makes explicit/geometry/auto mutually exclusive, returns an existing admitted child before reading mutable evidence or rebuilding candidates, and otherwise freezes the available automatic model pins in the existing atomic supplement admission.
- `ImageSupplementWorkflow.run(run: dict) -> dict` issues `select_image_enhancement`, derives (without mutating the admitted run) an explicit enhancement config only for a selected checkpoint, and never issues `verify_image` for auto selected/skipped/error paths.
- `EnhancementSelectionActivities.select_image_enhancement(run)` accepts a missing child `verify` checkpoint; the existing supplement baseline helper reads the frozen `source` parent snapshot.
- `SupplementActivities.finish_supplement(payload)` preserves the mapped frozen parent baseline for auto skip and selection error. Errors remain `partial` and cannot enter the human-completion gate; selected enhancement continues through existing fusion.

Verification:

- `.venv/bin/pytest -q backend/tests/test_adaptive_enhancement.py backend/tests/test_adaptive_enhancement_workflow.py backend/tests/test_supplement_workflow.py backend/tests/test_enhancement_conversation.py backend/tests/test_supplement_activities.py backend/tests/test_supplement_policy.py` — `58 passed`.
- Targeted Ruff over the five production files and related tests — clean.
- Root-agent integration run with real PostgreSQL/Temporal and model substitutes — `8 passed in 22.59s`, including linked selected/skipped, selection acknowledgement-loss recovery, frozen pins, unchanged parent, and no detector/original-review calls.

Limits retained: one existing supplement slot per parent; no schema, dependency, detector, original-image VLM, model/prompt, or fusion changes. Old runs without an `auto_enhancement` pin retain their prior command order.
