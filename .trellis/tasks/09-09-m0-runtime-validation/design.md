# Design

- Reuse current torch/CUDA through a project venv with system-site-packages; install project dependencies only inside it. Record this environment boundary explicitly.
- Native Temporal server + PostgreSQL on loopback; dedicated data directories below ignored var/. Infrastructure commands must target only project processes.
- Model adapter contains vendor-specific objects; typed detection outputs belong to the backend, never the workflow.
- First API adapter targets OpenAI-compatible chat-completions JSON responses via httpx, named as such rather than assumed universal. Endpoint/model/key are server configuration; validate returned JSON against the caller's schema. Transport tests do not claim live provider compatibility.
- CPU/network I/O stays in Activities; deterministic Workflow demonstrates a durable human wait. Tests own disposable workflow IDs and records.
