# T05 — Image inspection and conditional VLM workflow

Status: in progress; image workflow and UI implemented; local verification and independent review passed; real VLM pending configuration. T04 passed independent review and is frozen at jj 0c6d664b (change wtkqtltt). Its evidence is in ../archive/2026-09/09-09-m1-intake/.

Goal: connect an admitted original image task to a real Temporal inspection workflow: five-class primary detection, quality assessment, conditional VLM review/full-image negative audit, evidence-backed result and persisted progress.

Binding scope: DEVELOPMENT_PLAN.md T05 and main architecture sections 9–10. Use the existing DetectorAdapter, API client, AssetRef/Finding/TaskSnapshot/TaskPlan/ToolCall contracts and PostgreSQL intake. LLM/VLM run through APIs; no model training or extra orchestrator.

Acceptance:
- Submitted image executes on the existing local detector; shared contracts isolate model implementation. Input text never filters away the base five-class check.
- Conditional VLM handles uncertain/conflicting candidates and required negative audits; API failure or poor quality never becomes normal. OTHER stays human-reviewable, not automatically confirmed.
- Activities own I/O and model calls; Workflow remains deterministic. Persist progress and idempotent evidence/results so failures can retry without duplicate records. Decide explicitly how admitted T04 queued tasks are started; a DB row alone is not a running workflow.
- Unit/fault tests use explicit adapters; real GPU/API checks remain separate and truthful. Missing provider configuration must be visible, not silently replaced by fake results.
- Update the current T04 queued-only UI wording when actual execution is connected. Multi-turn follow-up/tool planning remains T06; video event processing remains T07–T09.

Next action: validate the chosen real VLM with user-provided local configuration. Real LLM/VLM provider/model/Base URL/local keys remain pending; never request keys in chat. Do not rerun already-completed T04 suites before a relevant change requires it.
