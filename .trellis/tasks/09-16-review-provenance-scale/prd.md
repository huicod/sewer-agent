# Review follow-up: provenance and detector evidence

User approved the preceding review's remediation on 2026-09-16; retraining remains a later human decision.

- Distinguish stored detector labels/scores, VLM advice, fusion actions and current findings in API/UI/HTML/JSON reports, including already stored image tasks. Never reinterpret an old-class score as revised-label confidence.
- Correct current documentation, not historical experiment records.
- Fix explicit `_frameNN` source grouping; preserve original data and historical split evidence. Freeze reproducible diagnostic manifests without claiming reconstructed/new splits are proven unseen by existing weights.
- Compare source and SEDD with common area bins; run a bounded full-image versus overlapping-tile YOLO control with saved predictions and representative visual results. No VLM/LLM calls, training, new dependencies, online policy changes or service restart.
- Tests cover read-only evidence, revisions, missing/legacy reviews, grouped frames and evaluator coordinate/matching behavior. Record actual results and remaining independent-test limitations before the user's training decision.

Base: `f5564f62`; local colocated jj change, no Git worktree or automatic push.
