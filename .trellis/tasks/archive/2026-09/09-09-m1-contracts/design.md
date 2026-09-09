# T03 design

- Flow: API/LLM JSON → Pydantic boundary validation → typed plan/evidence → Temporal activities/storage → same typed progress/results → UI/report. Contracts do not execute or persist state.
- ContractModel is frozen, forbids extra fields and nonfinite floats, strips string whitespace. Asset references use UUID plus positive version; no arbitrary filesystem paths or secrets in public contracts.
- TaskPlan separates requested class focus from mandatory full five-class detection. Ordered steps declare dependencies; supplied order must be topological. Runtime gating/execution belongs to T05/T06, not a second scheduler here.
- Finding labels reuse DetectionLabel plus OTHER/None; confirmation provenance distinguishes rules and human. VLM suggestions are not state transitions. TaskSnapshot enforces consistency, not transitions: partial coverage can coexist with confirmed defects, while normal requires sufficient coverage/quality, completed negative audit and no unresolved findings.
- Engineering defaults: at most 32 tool calls per plan, 3 transport attempts; event evidence/open scans/semantic reviews default to 1/1/2 with the architecture's caps. These are configurable bounded contracts, not an API cost guarantee. Evidence stores explicit asset/version, optional original-image region and paired video frame/timestamp.
- No new dependencies or file hashing. Reuse detection geometry validation instead of introducing divergent bbox rules. T02 remains open solely for live API integration.
