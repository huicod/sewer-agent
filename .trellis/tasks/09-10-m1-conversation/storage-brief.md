# T06-B1 storage contract
Read first; only implement storage + public turn models, not workflow/UI/API. Read AGENTS.md and backend conventions. No nested agents/VCS/network/secret dumps. Use TDD and focused tests.
Allowed writes: backend/src/pipe_inspector/contracts/turns.py; backend/src/pipe_inspector/storage/conversation.py; backend/tests/test_turn_contracts.py; backend/tests/integration/test_conversation_storage.py; storage-report.md here.

Types (ContractModel):
TurnInput: request_id UUID, text str 1..4000, input_asset AssetRef|None=None, task_id UUID|None=None.
TurnSubmission(TurnInput): session_id UUID.
TurnRecord(TurnSubmission): created_at/updated_at AwareDatetime, status Literal queued/running/completed/failed default queued, stage Literal queued/planning/executing/finished default queued, reply str|None default None max4000, linked_task_id UUID|None=None, error_code str|None=None max100, attempts int >=0 default0. completed requires nonempty reply and stage finished; failed requires error_code and stage finished. Nonterminal cannot have error_code. Extra input fields forbidden.

ConversationRepository extends InspectionRepository; initialize() super plus sewer_conversation_turns(request_id PK,session_id FK,created_at,submission JSONB,record JSONB,config JSONB,checkpoints JSONB DEFAULT '{}'). No schema mutation on import.
- admit_turn(submission:TurnSubmission, config:dict)->TurnRecord. Duplicate request_id+identical submission returns original with first config, even if process config differs; mismatch SubmissionConflict. Serialize admission per session with SELECT FOR UPDATE on sewer_sessions. New turn while another queued/running in same session raises ConversationBusy (define here). Separate sessions allowed. Validate input_asset exists/is original image or video; task_id belongs same session; if both given, asset must match task input. Task references cannot cross sessions. Original uploads follow the existing global, single-user asset model (no uploader ownership/authentication); task-based foreign-asset checks are defensive, not private media isolation.
- get_turn(request_id)->TurnRecord; list_turns(session_id,limit=50)->tuple chronological last limit (1..100); missing session MissingRecord.
- get_turn_config(request_id)->dict.
- load_turn_step(request_id,step)->dict|None; save_turn_step(request_id,step,payload)->dict first-write cached return; same step retry never replaces payload.
- update_turn(request_id, **changes)->TurnRecord. Allowed fields only status/stage/reply/linked_task_id/error_code/attempts. Lock row, reconstruct validated model with updated_at now; immutable submission. Terminal records return existing for repeated updates, never regress. If linked_task_id set ensure task exists in same session. Refuse attempts regression (or retain max). Never leak DSN.

Tests: RED missing feature then real-PG tests in UUID-named test schema (follow test_inspection_storage style, PG is running); reopen read, request retry, mismatch, same-session concurrency, cross-session/task/asset ownership, terminal stability, checkpoint first-writer, no server credential data. SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_conversation_storage.py backend/tests/test_turn_contracts.py. Only drop your test schema. Root will wire app/init scripts and full tests.
