# T06-B1 durable conversation implementation
Goal: a visible conversation + upload vertical slice: persist turns, parse intent in Temporal, launch image inspections or query stored evidence/reports; refresh and retry preserve identity.
Architecture: existing FastAPI/Temporal/PostgreSQL modular backend. One active turn per session; no new scheduler. Linked inspection tasks outlive a conversation response. Existing image workflow remains responsible for detection/result.
Spec: design.md and main architecture sections 9–10. T06-B1 does not claim all of T06 complete: linked supplemental re-review remains B2, not silently performed as a new detector pass.

Global constraints: LLM/VLM APIs; all five classes always detected; OTHER needs human confirmation; no arbitrary paths/URL/tools/budget from model; no secret in Temporal/DB; no new runtime dependency. Chat read requests never rerun detection. Initial image inspect can force VLM when text explicitly requests verify. Unsupported tools/video execution and supplements return explicit limitations, no fake success.

- [x] Storage subtask: TurnInput/TurnSubmission/TurnRecord and ConversationRepository, tests first; exact interfaces in storage-brief.md. Preserve existing tables via additive DDL.
- [x] UI subtask: session conversation panel, optional upload/task selection, strict validation, idempotent resend and refresh/polling; frontend-brief.md defines endpoint contract.
- [x] Root: failing tests for admission/start, Temporal Activities/Workflow, inspect vs query/report routing, force_review and unavailable-tool refusal. Implement in workflow/conversation.py, workflow/conversation_activities.py, agent/conversation.py, api/conversation.py; wire app and worker/storage launchers.
- [x] Root: local PG/Temporal end-to-end with fake APIs, lost-response/checkpoint retry, bounded real Qwen conversation; root integration and independent review; evidence in conversation-report.md.

Interface scan: storage produces shared turn models plus repository APIs; root and UI consume the exact flat JSON contract below. Root alone edits API/worker/current image workflow; storage only new storage/contracts files; UI only frontend. No concurrent shared-file writes. Model-generated message is a proposal, not authoritative result: queries render stored evidence/status and typed server report links.
