# T04 design — conversation-ready media intake

Approved scope: DEVELOPMENT_PLAN.md T04, plus the user's confirmed dialogue/file-upload entry. This implements intake, not the LLM dialogue executor (T06) or inspection workflow (T05).

## Decisions

- Retain FastAPI + PostgreSQL + local assets; no second orchestrator. T04 persists queued task projections only. Browser disconnect/reload has no task side effect.
- A session groups submissions and their evidence. T04 stores each initial user instruction with its task; T06 adds read-only follow-up answers and explicit linked tool runs. Do not overwrite completed task evidence or treat each question as a new full inspection.
- Prefer separate upload then idempotent task submission over one large combined request: a network retry of task creation reuses its asset and request UUID. No generic chat framework or message/event store is needed in T04.
- Plain psycopg, three prefixed business tables (sessions/assets/tasks), UUID foreign keys and JSONB validated payloads. Explicit idempotent schema-init script, not DDL on import/request. Short connection-per-operation transactions; no ORM or migration framework for the initial schema.
- Originals stored as asset_root/<UUID>/1/source, with server-generated IDs; client filenames are not paths. JPEG/PNG fully decoded; MP4/MOV/AVI/MKV metadata probed and first video frame decoded using bounded FFmpeg subprocesses, file-only protocols and container allowlist. Full video decode/coverage validation belongs to T07; admission is not proof of an intact whole video.
- Configurable default upload 1 GiB, image/video frame limit 40 million pixels, probe/decode timeout 20 seconds. Content-length and streamed body limits precede multipart completion; uploaded file bytes are checked independently. Invalid inputs remove only that request's temporary asset.
- Minimal Chinese React/TypeScript/Vite page: session, upload + instruction, task cards and server progress/error. Persist session ID for refresh recovery; poll only active tasks. Queued text explicitly says intake accepted, execution not connected in T04. No fake assistant response.
- Local-only development server; no public exposure or authentication claims. Frontend uses same-origin /api proxy; never contains server secrets.

## Shared interface (binding for parallel work)

contracts/intake.py:
- SessionRecord(session_id: UUID, created_at: AwareDatetime)
- TaskSubmission(request_id: UUID, session_id: UUID, input_asset: AssetRef, instruction: stripped str of length 1..4000)
- TaskRecord(request_id, session_id, input_asset, instruction, created_at, snapshot: TaskSnapshot); snapshot.task_id is the task identifier.
- SessionDetail(session: SessionRecord, tasks: tuple[TaskRecord, ...])
- All inherit ContractModel; no arbitrary paths, code or client-written runtime status.

storage/repository.py: PostgresRepository(dsn: SecretStr), initialize(), create_session() -> SessionRecord, get_session(UUID) -> SessionDetail, save_asset(AssetRecord) -> None, get_asset(AssetRef) -> AssetRecord, create_task(TaskSubmission) -> TaskRecord, get_task(UUID) -> TaskRecord. MissingRecord and SubmissionConflict exceptions have safe fixed messages. Same request_id + same submission returns the existing task; differing payload is conflict. Missing session/asset rejected atomically. Runtime status created queued/pending/queued with zero progress and UTC timestamps.

HTTP:
- POST /api/sessions (no body) -> 201 SessionRecord
- GET /api/sessions/{session_id} -> SessionDetail
- POST /api/assets multipart field file -> 201 AssetRecord
- GET /api/assets/{asset_id}/versions/{version} -> AssetRecord
- GET /api/assets/{asset_id}/versions/{version}/content -> original bytes, server MIME
- POST /api/tasks JSON TaskSubmission -> 201 TaskRecord (also same-request replay)
- GET /api/tasks/{task_id} -> TaskRecord
- GET /health remains liveness only. Errors: 404 missing, 409 conflicting replay, 413 oversized, 415 unsupported/corrupt, 422 schema, 503 unavailable local service/tool. Application errors use {detail: {code, message}}; clients handle FastAPI validation arrays without dumping raw payloads.

## Acceptance

Real PostgreSQL test in a unique test-owned schema proves reconnect persistence, references and duplicate submission behavior without changing Temporal databases. Real JPEG/PNG and synthetic video exercise content validation and byte preservation; invalid/oversize/traversal/unavailable cases fail honestly. UI tests cover upload/submit, reload recovery, error states and queued progress. Full backend tests, Ruff, frontend tests/build and one scoped independent review close T04. No paid API/GPU rerun is needed.
