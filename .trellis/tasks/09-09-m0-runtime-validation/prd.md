# T02 — Runtime validation

Requirement source: project_management/DEVELOPMENT_PLAN.md M0/T02 and main architecture sections 9–10.

Deliverables: reproducible five-class GPU inference using existing weights; structured LLM/VLM client with bounded errors; persistent Temporal + PostgreSQL native deployment and restart test.

Acceptance: real raw image inference with model/version/class map; local API transport tests including malformed/timeout/limit responses plus separately reported live API status; a workflow waiting for a signal survives service/worker restart and completes afterward.

No model training, external media uploads without configured API, invented credentials, Docker requirement, or replacement of durable execution with an in-memory queue. API provider and live CCTV videos are still pending user input; independent local work continues.
