# T03 implementation plan

Goal: typed, validated boundaries for the next image pipeline. Stack: Python 3.12/Pydantic 2. Spec: main architecture section 10; requirements in prd.md. Use TDD/subagent-driven-development under the repository's Trellis/jj overrides, not a separate ledger.

- [x] Root: tests for AssetRef/AssetRecord, ImageRegion, Finding and TaskSnapshot, then minimum implementations. Cover invalid geometry and class/status separation, human-only OTHER confirmation and normal eligibility; verify JSON round trips.
- [x] Planning implementer: follow planning-brief.md, first failing test then planning.py/tools.py. No I/O or dispatcher.
- [x] Detector implementer: follow detector-brief.md, add capabilities/load/warmup and prove test replacement; avoid repeating real GPU smoke.
- [x] Root: integrate exports, check whole backend suite once, scoped diff review and resolve findings, save report and jj checkpoint. Then continue to T04 upload/task API and minimal Web shell.

Example acceptance: TaskPlan.model_validate({"steps": []}) must fail; a snapshot with inspection_result="normal" and negative_audit_complete=False must fail; a confirmed OTHER with confirmed_by="rules" must fail. Neither validation may import Ultralytics or contact APIs.
