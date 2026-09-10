# T08 Implementation Plan

Goal: durable conversational video inspection and a measured, separately reported paired image pilot.
Architecture: existing Temporal/PG + serial GPU worker; T07 core reused, CPU/API evidence review; no new scheduler/dependency.
Spec: prd.md, design.md, main architecture §§9–10. Trellis is the only ledger; root owns integration/jj. Existing source a60a3a9a, working change nxyslwxv. Execute with TDD and scoped delegates, repository jj overrides apply.

## 1. Checkpoint storage (isolated delegate)

Files: new `storage/video.py`, `tests/integration/test_video_storage.py` only. `VideoRepository(SupplementRepository)` reuses all prior APIs and adds:

- `ensure_video_state(task_id:UUID, state:VideoAggregationState) -> VideoAggregationState`: requires admitted VIDEO source and matching source/config/model pins on reopen; insert empty state only once.
- `load_video_state(task_id:UUID) -> VideoAggregationState|None`.
- `commit_video_chunk(task_id:UUID, observations:tuple[FrameObservation,...], *, attempt:int=1) -> VideoAggregationState`: lock task/state, deterministic aggregate_chunk on saved state, atomic append chunk+head+snapshot counters; duplicate fully committed prefix returns stored state, gap/overlap/version mismatch rejected. Do not overwrite terminal snapshots or finalized heads. Store frame range and observations in chunk row.
- `get_video_frame(task_id:UUID, frame_index:int) -> FrameObservation`: original accepted observation, MissingRecord if absent.
- `finish_video_scan(task_id:UUID, *, error_code:str|None=None) -> VideoScanResult`: atomically flush active events, freeze head and immutable `video_scan` step; completed coverage only when error absent and frames exist; idempotently return cached result.

Use own two tables for state/chunks plus existing step table; initialize explicitly, never at request/import. No workflow/API/settings changes in delegate. Tests: real isolated PG schema, two contiguous chunks+replay, source/config/gap refusal, snapshot counters, missing frame, finalization/late commit. RED missing module → GREEN.

## 2. Resumable scan/evidence and execution (root)

Files: existing video decoder/scan/contracts, new `workflow/video.py`, `workflow/video_activities.py`; focused unit/integration tests. Extend decoder with `start_frame=0` and optional `on_progress(index, timestamp)` callback before skip to heartbeat/cancel during prefix replay. Extend scan_video with saved initial_state and on_chunk returning committed state (None remains valid for existing callers). Reject saved source/config/model mismatch; no inference on committed prefix. Preserve T07 CLI behavior.

Reuse current config pins and add video policy/config, chunk size=8, review budget=32. GPU scan Activity resumes from DB, emits heartbeat and safe tool records, commits each batch; network/worker failure retries finite. CPU evidence step creates deterministic run/frame assets from selected originals without re-inference; frames already persisted reused. Workflow never puts all frame data in Temporal history. Update `request_cancel` acknowledges admission, stops active work and finalizes cancelled snapshot without deleting evidence; reporting closes cancellation admission explicitly (review correction).

Tests: saved prefix resumed at next frame with no old detector calls; cancellation and source/pin mismatch; real PG+Temporal worker replacement and duplicate start; saved results survive failed final write. Existing mock adapters only for deterministic faults; real video+GPU explicit later.

## 3. Video decision/entry/UI integration (root or isolated delegate after contracts fixed)

Files: new `agent/video_policy.py` (+tests), existing workflow/start, agent/conversation, API inspection/app, worker/init scripts; frontend TaskCard/API/tests. Use original per-frame observations and ImageReviewer decisions to resolve event evidence, retain conflicts/OTHER/additional suggestions pending, no normal on insufficient coverage/quality/review budget. Tool order and maximum attempts remain server-owned.

Start routes admitted video to VideoInspectionWorkflow; image/supplement paths preserved. Add POST `/api/tasks/{id}/cancel` for video, current terminal result read-only; GET `/api/tasks/{id}/video` returns saved scan/evidence metadata without executing tools. UI shows real frame/chunk counts, cancel and evidence links; no fabricated replies or public service binding. Full human review stays T09.

Tests: video conversation actually dispatches, required text tools not silently dropped, repeated start/cancel and terminal behavior; known image regression; frontend HTTP boundary and build. RED → GREEN per unit.

## 4. Real data and handoff

- [x] Read-only schema inventory, safe extraction and GT-only sampling; 42 images / 39 real image reviews. Source provenance and scoped raw/queue/confirmed metrics in data-report.md; no overall gain.
- [x] Full 561-frame and 3,698-frame real CCTV runs, including actual SIGTERM/recovery on the first. Coverage, candidate/evidence reduction and limits in runtime-report.md; no unlabelled video precision claim.
- [x] One independent scoped review; three consequential findings fixed with regressions. Final backend 513 passed/7 skipped, frontend 32 passed/build passed; save Trellis/jj handoff without automatic push.
