# Design

- A multi-stage Dockerfile builds the React frontend with Node and installs the Python application plus GPU/enhancement/geometry dependencies. All application roles use the same image and /app working directory.
- Compose separates postgres, temporal, initialize, api, cpu-worker, gpu-worker. Postgres has independent application and Temporal roles/databases. Matching Temporal admin-tools jobs install its two schemas and namespace; initialize owns business tables. Startup uses health/success dependencies.
- Persistent named volumes: PostgreSQL data and media. A read-only host model bundle mounts at /models for all application roles. No host-native .env or var mounts. GPU reservation is limited to the GPU worker, which retains serial execution.
- A dedicated container entrypoint constructs a DSN without leaking credentials, validates config/models, initializes tables, and execs API/worker processes. Readiness checks are bounded and distinguish liveness from dependencies/model files.
- Configuration helper creates a separate ignored .env.docker with generated database passwords; users edit provider fields and select the model bundle directory. Existing .env is untouched.
- Model manifest maps the seven active models to deterministic bundle paths and original sources. Helper validates/copies/downloads/uploads only allowlisted assets, preserving the HF metadata snapshots and provenance. Upload waits for HF account/target input.
- Docker validation must be honest: local host is a restricted nested container without daemon/socket. Obtain Compose config validation, test initialization/preflight failures and real local persistence using isolated test schemas; attempt container build/run only when supported, otherwise add CI checks and document remaining GPU acceptance.
