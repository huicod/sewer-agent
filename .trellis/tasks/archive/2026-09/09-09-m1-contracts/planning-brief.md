# T03 planning/tool contracts — implementation brief

Implement directly, TDD, no nested agents or version-control mutations. Only own `backend/src/pipe_inspector/contracts/planning.py`, `tools.py`, `backend/tests/test_planning_contracts.py` and this task's `planning-report.md`. Do not edit exports/common/assets/root tests. Root provides `common.ContractModel`, `common.NonEmptyStr`, and `assets.AssetRef(asset_id: UUID, version: int=1)`; wait/message if needed, do not stub these production modules yourself.

## Exact public contract

`planning.py`:
- ActionKind StrEnum: detect, verify, open_scan, enhance, segment, relative_measure, compare, retrieve_standard, summarize, export_report. These are plans only, not claims all tools are installed.
- OutputFormat StrEnum: json, html.
- CallBudget: max_tool_calls int default32 range1..128; max_transport_attempts default3 range1..5; per_event_supplement default1 range0..1; per_event_open_scan default1 range0..1; per_event_verifications default2 range0..2.
- PlanStep: step_id NonEmptyStr; action ActionKind; depends_on tuple[NonEmptyStr,...]=(); required bool=True. No loose parameters, commands or paths.
- TaskPlan: schema_version Literal["1"]= "1"; target_labels tuple[DetectionLabel,...] default all five; open_anomaly_audit bool=False; steps tuple[PlanStep,...] REQUIRED; outputs tuple[OutputFormat,...]=(json,html); budget CallBudget defaultfactory.
- Validate nonempty unique target labels and outputs; unique step IDs; exactly one detect step at position0, required and dependency-free; other steps must have a dependency path to detect; all dependencies unique and refer to earlier steps (thus reject self/cycles/dangling IDs). relative_measure needs a segment ancestor. Number of steps <= max_tool_calls. A target_labels subset is reporting focus, never changes detect's full class space. Export helper not needed.

`tools.py`:
- ToolResource StrEnum cpu/gpu/api. ToolCallStatus StrEnum queued/running/succeeded/failed/skipped/uncertain.
- ToolError: code NonEmptyStr; retryable bool; message NonEmptyStr (caller must supply safe text, no raw API body).
- ToolCall: call_id UUID, task_id UUID, step_id NonEmptyStr, idempotency_key NonEmptyStr, attempt int>=1 default1, tool_name ActionKind, tool_version NonEmptyStr, model_version NonEmptyStr|None=None, resource ToolResource, timeout_seconds float>0 finite, input_assets tuple[AssetRef,...], output_assets tuple[AssetRef,...]=(), status ToolCallStatus defaultqueued, error ToolError|None=None.
- failed/uncertain require error; succeeded/queued/running must not carry error; skipped requires a nonempty skip_reason and no error; other statuses must not carry skip_reason. Failed/skipped/uncertain may preserve partial outputs. No client retry implementation or transition engine.

## Tests and evidence

Test empty/missing detect, unknown action/extra command/path fields, forward/self/cyclic/dangling dependencies, duplicate step IDs, valid transitive segment→relative_measure, missing segment dependency, budget overflow, valid target class focus; JSON roundtrip. For tool records test safe structured failure, uncertain retry metadata, skipped reason, invalid status/error combinations, invalid attempts/nonfinite timeout. Use literal input dictionaries and real Pydantic validation; no mocks necessary.

Run focused pytest and ruff only for owned files. Record RED/GREEN output, exact files, any ambiguity in planning-report.md; root handles full integration and review.
