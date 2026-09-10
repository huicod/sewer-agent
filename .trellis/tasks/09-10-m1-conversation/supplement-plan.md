# T06-B2 linked image re-review implementation plan

Goal: an existing completed/partial image can receive one durable, linked VLM re-review via conversation; original task/report remain unchanged.
Architecture: child TaskRecord(parent_task_id) + atomic PostgreSQL admission/checkpoints + dedicated Temporal verify/finalize workflow. Reuse ImageReviewer and image finalization with a small decision hook. No GPU detection, no new orchestrator or dependency.
Spec: main architecture sections 9–10, DEVELOPMENT_PLAN.md T06, task design.md. Trellis is the only plan/progress store; scoped approval is already given. Follow TDD and independent review; jj changes, not Git worktrees/commits.

## Boundaries

Only supplement intents containing verify (plus optional summarize/export_report) are executable. Other requested tools cause explicit no-execution reply. Parent must be a same-session original image task, completed/partial, with valid saved detection/run. Exactly one child per original task, including failed attempts; duplicate request reuses it, a new request cannot evade budget via child chaining. Running, failed-before-detection, cancelled, video and new-attachment-only requests cannot be supplemented. Semantic tool selection is probabilistic and bounded; no new private multiuser asset model.

Child copies original observation and source snapshot/review in the same transaction as its identity, relation and pinned run. Current VLM settings may differ from the original run but cannot change during child retries. Required re-review failure is partial/inconclusive or retains established defects, never normal. Prior VLM-only/OTHER candidates remain pending; conflicting substantive candidate decisions stay pending. Abstain/missing original review can be resolved by new evidence-consistent advice. No guarantee of effect improvement or exactly-once provider billing.

## Tasks and shared interfaces

1. Storage delegate owns contracts/intake.py (TaskRecord.parent_task_id default None; reject self link), storage/inspection.py (preserve parent field on snapshot updates), new storage/supplements.py and associated new tests. Exact admission interface in supplement-storage-brief.md. Root does not edit these concurrently.
2. Root owns agent/supplement_policy.py, workflow/supplement.py, workflow/activities.py (decision hook), workflow/start.py (pinned child routing and no accidental detect), agent/conversation.py, app/init/worker registration, reports.py and frontend parent-link display. Child-only verify/finalize uses existing image checkpoints/tool attempts and final DB retry semantics.
3. Root integration: real local PG/Temporal with fake external provider, demonstrate conversation→child→query/report, original untouched, one semantic operation across retries, no GPU worker available. One optional selected-image live VLM check only if this adds new protocol evidence; not required to repeat an unchanged ImageReviewer. Final scoped independent review, regression and journal/jj checkpoint.

Checks:
- [x] Storage RED/GREEN: atomically persist relation/run/observation, reopen, duplicate identity, mismatched request, concurrent budget, invalid parents, parent link retained on updates.
- [x] Policy RED/GREEN: normal after missing initial review; old OTHER/additional candidates and substantive conflicts cannot disappear; errors cannot produce normal; source unchanged.
- [x] Dispatch/start RED/GREEN: only supported supplement actions, no asset substitution, fixed workflow ID; manual start of child routes to re-review and never detector; startup ambiguity retains child link.
- [x] UI/report RED/GREEN: show child→original relation and truthful re-review progress; legacy tasks still render; parent UUID validation.
- [x] Integration/final: local PG/Temporal recovery/duplicate/review-only execution, final backend and frontend tests/build, independent review, progress handoff.

Interface scan: storage produces parent_task_id + SupplementRepository.admit_supplement; root consumes exact names. Admission and workflow both use config operation=supplement_review, parent_task_id, force_review=True and supplement_policy_version=supplement-policy-v1. Existing TaskSubmission stays unchanged (no client-supplied parent field). Frontend/report consume only the optional server-owned parent link; DB update preserves it. The source checkpoint eliminates reads of mutable live parent state during child finalization.
