# T10 integrated code review

Reviewed the working-copy diff from `ba3da925` (snapshot `2c9c0979`) against
`prd.md`, `design.md`, and `implement.md`. Review mode was read-only except for
this report.

## Strengths

- The feature is genuinely opt-in: legacy run histories have no new workflow commands, while an
  explicit unavailable `open_scan` is rejected before task admission.
- Selection is persisted before GPU work; successful scan checkpoints are reused; missing,
  deferred, failed, or interrupted selected scans prevent a complete negative audit.
- Fusion preserves prior findings and confirmation state, creates stable review-only candidates,
  and validates source dimensions plus detector/model/vocabulary metadata before persistence.
- The adapter uses a fixed defect-only vocabulary, explicit local model assets, preflight checks
  that avoid Ultralytics' request-time installer path, CPU text encoding, bounded output, and
  serial primary/open model unloading through the single GPU worker.
- Main-agent evidence reports 10 real PG/Temporal integration cases, 2 human-wait cases, and a
  successful two-image local GPU compatibility smoke. The observed 32 MiB residue was isolated to
  the primary model's cuBLAS workspace, not retained open-model state; no accuracy gain is claimed.

## Critical

None.

## Important

1. **Distinct named `OTHER` concepts are incorrectly deduplicated.**
   [`backend/src/pipe_inspector/agent/open_scan.py:42`](/root/autodl-tmp/server/backend/src/pipe_inspector/agent/open_scan.py:42)
   treats matching `canonical_label` as matching class. Both fixed open-vocabulary concepts
   `root intrusion` and `water infiltration` map to `OTHER`, so overlapping boxes suppress the
   second candidate before human review even though their names represent different defects. A
   local reproduction with two identical boxes produced two candidates but one finding, retaining
   only `root intrusion`. This conflicts with additive named-`OTHER` review semantics. For
   `OTHER`, dedupe must also require the same normalized open name; the five canonical classes can
   retain canonical-label matching. Add a regression covering overlapping distinct `OTHER` names.

2. **The VLM-suspected trigger is silently gated by primary image quality.**
   [`backend/src/pipe_inspector/workflow/open_scan_activities.py:111`](/root/autodl-tmp/server/backend/src/pipe_inspector/workflow/open_scan_activities.py:111)
   and
   [`backend/src/pipe_inspector/workflow/open_scan_activities.py:148`](/root/autodl-tmp/server/backend/src/pipe_inspector/workflow/open_scan_activities.py:148)
   require `quality.adequate` for `additional_anomalies` or `need_more_context` to select an open
   scan. The approved design defines VLM suspicion as an independent trigger; only the automatic
   negative-result trigger is quality-gated. A local image-path reproduction with an assessable
   VLM `additional_anomalies` result and `adequate=False` persisted `not_triggered`. Separate these
   conditions and cover both image and materialized-video VLM-suspicion paths.

3. **Video open scans ignore the run-pinned transport-attempt budget.**
   [`backend/src/pipe_inspector/workflow/video.py:40`](/root/autodl-tmp/server/backend/src/pipe_inspector/workflow/video.py:40)
   fixes every `_execute` retry policy at three attempts, and the new open call at
   [`backend/src/pipe_inspector/workflow/video.py:105`](/root/autodl-tmp/server/backend/src/pipe_inspector/workflow/video.py:105)
   does not override it. Therefore `plan.budget.max_transport_attempts=1` or `5` is honored by the
   image open path but not the video path. Thread the pinned value into video open-scan execution
   and add a workflow assertion for a non-default attempt cap.

## Minor

None beyond the missing regression cases attached to the Important findings.

## Verification

- `pytest` on the five new/changed unit suites: **71 passed**.
- Adjacent image/start/conversation/human/turn workflow suites: **68 passed**.
- Ruff on all changed production Python paths and the worker: **passed**.
- Two deterministic local reproductions confirmed Important findings 1 and 2.
- This reviewer did not rerun PostgreSQL/Temporal integration, GPU smoke, frontend, network, or API
  checks; the main agent owns and supplied those results.

## Recommendation

Fix the three Important issues and rerun their focused regressions plus the existing T10 unit and
PG/Temporal integration suites. No wider schema, platform, model-accuracy, or strategy work is
recommended in T10.

**Ready to deliver: With fixes**
