# Qwen integration — 2026-09-10

Historical failed probe; the later successful SDK, text and original-image checks supersede the blocker below: [qwen-live-success.md](qwen-live-success.md).

- User choice: qwen3.8-max; local DASHSCOPE_API_KEY shared by explicitly configured LLM/VLM roles. No credential values persisted in this report or history.
- Official reference: https://help.aliyun.com/zh/model-studio/qwen3-8-max ; region reference: https://help.aliyun.com/zh/model-studio/base-url .
- One actual text request to https://dashscope.aliyuncs.com/compatible-mode/v1, thinking disabled, max output 256: authentication_failed. No CCTV image was sent and no region/provider fallback was attempted.
- Required alignment: Key-issuing platform and matching API Base URL. Model support and defect accuracy are not validated by the failed request.
- Offline regression: 236 passed, 22 explicit integration tests skipped; Ruff passed. Earlier T05 GPU/Temporal evidence remains in runtime-report.md and recovery-report.md.
- Added shared-key host guard, role-key precedence, optional output cap/thinking flag and durable parameter pins. T05 remains in_progress for live acceptance; T06 independent software work may proceed.
