# T05 implementation plan

Spec: design.md. Trellis controls state; jj mxopyywt isolates T05. No duplicate Superpowers ledger. User approved continuing this roadmap task; no repeated approval gate.

- [x] Vision agent: write failing tests, implement the exact shared inspection contracts + quality/conditional review/decision functions and VLM JSON boundary; scoped tests/Ruff, vision-report.md.
- [x] Storage agent: failing real-PG tests in a unique schema; implement InspectionRepository run pins, call attempts, successful checkpoints and validated projections; scoped tests/Ruff, storage-report.md.
- [x] Root: failing start/Activity/Workflow tests, implement pinned image execution and worker launcher; connect explicit idempotent start and result/call queries to API/UI. No implicit starts of T04 backlog or fake Temporal execution.
- [x] Root: real dedicated-queue Temporal integration with adapters; real checkpoint-backed GPU run in the production workflow (not repeated standalone compatibility smoke); safe failure and missing-VLM behavior. API key remains external configuration.
- [x] Integrate related/full tests, frontend build, one independent review; fix only real findings. Update runtime instructions, roadmap and Trellis journal. T05 remains partially validated if real VLM configuration is still absent.

Interface scan: vision agent outputs ImageObservation/ImageReview/InspectionDecision; root consumes JSON serialization at Activity boundaries. Storage consumes existing TaskSnapshot/ToolCall and plain dict checkpoints, so it can proceed independently. Root alone owns integration files/plan/VCS. Thresholds are provisional and configurable only through explicit versioned policy changes, not claimed calibrated.



Open acceptance item (2026-09-10): qwen3.8-max selected; shared Key wiring is implemented. One bounded Beijing text call failed authentication; no CCTV upload or cross-region retries. Await the Key's actual provider/Base URL before live VLM and sample-quality assessment. Implementation/local tests/review are passed; do not archive as fully accepted yet.
