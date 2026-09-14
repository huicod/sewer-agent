# Task 1 backend implementation report

## Scope and result

Implemented the pinned `video-selection-v1` backend path without changing the existing
`video-review-v1` verdict policy or the unpinned legacy `select_review_frames` ordering.
The new path builds a bounded, time-balanced pool, keeps required event evidence in complete
bundles, reserves roughly one quarter of pool/review capacity for periodic audit, optionally
uses video-only scene previews, deducts scene calls from the same semantic request budget, and
persists the final selection alongside every materialized original-PNG evidence artifact.

Owned files changed:

- Added `contracts/video_selection.py`, `agent/video_selection.py`, and
  `workflow/video_selection.py`.
- Modified `workflow/video_activities.py` and `workflow/start.py`.
- Added `test_video_selection.py` and `test_video_selection_activity.py`.
- Added this report.

Necessary scope expansion was raised to and approved by the main agent before editing:

- `storage/inspection.py` adds keyword-only `allow_error_checkpoint=False`. The default still
  accepts only succeeded/skipped calls; the explicit opt-in additionally accepts only
  failed/uncertain terminal calls so a scene error fallback and its final call record commit in
  one transaction. Running/queued calls remain forbidden.
- `integration/test_inspection_storage.py` covers the default rejection, explicit atomic error
  checkpoint, and transaction rollback on an unserializable payload.

No dependency, model, prompt used by `ImageReviewer`, detector/decoder/event aggregation,
workflow command, API endpoint, service, paid call, GPU run, or VCS state was changed by this
implementation agent.

## Frozen callable contracts for the live harness

```python
VideoActivities(
    settings,
    repository,
    media,
    *,
    detector=None,
    reviewer=None,
    scene_selector=None,
)

VideoSceneSelector(client, max_image_bytes: int = 8_388_608)

await VideoSceneSelector.classify(
    frames: Sequence[tuple[int, Path]],
    *,
    heartbeat: Callable[[int], None] | None = None,
) -> VideoSceneBatch

build_candidate_pool(
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    *,
    force_review: bool = False,
    budget: int = 32,
) -> tuple[int, ...]

select_video_review(
    scan: VideoScanResult,
    observations: dict[int, FrameObservation],
    assessments: Sequence[VideoSceneAssessment] = (),
    *,
    force_review: bool = False,
    budget: int = 32,
    scene_calls: int = 0,
    scene_errors: Sequence[str] = (),
) -> VideoReviewSelection
```

`VideoActivities.scene_selector` is public for recorded live/test injection. The default scene
client is constructed independently of `ImageReviewer`, caps output at
`min(api_max_output_tokens or 4096, 4096)`, and is enclosed by a 20-second whole-generate-call
deadline. Preview generation is PNG-only, bounded by source bytes and a 512-pixel longest edge;
the request includes original/preview dimensions and the actual response schema but no local
path. Heartbeats occur before/after each preview, around calls, and between batches.

## TDD evidence

RED failures were observed before their corresponding production changes:

1. `.venv/bin/python -m pytest backend/tests/test_video_selection.py -q`
   - Collection failed with `ModuleNotFoundError: pipe_inspector.agent.video_selection`.
2. The audit-only capacity regression failed with `assert 6 == 8`, proving selected audit IDs
   were being reused instead of filling remaining slots.
3. `.venv/bin/python -m pytest backend/tests/test_video_selection_activity.py -q`
   - Collection failed because `classify_scene_batches` did not exist.
4. The scene-error atomicity test failed because the checkpoint save received `call=None`,
   proving the error call/checkpoint were separate operations.
5. The prepare activity test failed because legacy materialization produced frame IDs
   `{1, 3, 9}` instead of the new pinned pool `{1, 3, 7, 9}`.
6. The missing-scene-configuration test failed with `AttributeError` while constructing the API
   client instead of persisting an unknown fallback.
7. The preview-heartbeat test failed with `unexpected keyword argument 'heartbeat'` before
   heartbeat support was added to the real preview loop.

GREEN progression:

- Pure contracts/selector/selection slice: `10 passed`.
- Pure + activity/checkpoint slice after atomic fallback and capacity fixes: `18 passed`.
- Final scoped CPU command:

  ```text
  .venv/bin/python -m pytest \
    backend/tests/test_video_selection.py \
    backend/tests/test_video_selection_activity.py \
    backend/tests/test_video_policy.py \
    backend/tests/test_inspection_start.py -q
  ...................................................... [100%]
  54 passed in 0.96s
  ```

- Scoped Ruff command over all Task 1 production/tests and the approved storage expansion:
  `All checks passed!`.
- Targeted opt-in PostgreSQL atomicity command, using the tests' unique disposable schema:

  ```text
  SEWER_RUN_STORAGE_TEST=1 .venv/bin/python -m pytest \
    backend/tests/integration/test_inspection_storage.py::test_error_checkpoint_requires_opt_in_and_commits_atomically \
    backend/tests/integration/test_inspection_storage.py::test_error_call_rolls_back_when_checkpoint_cannot_serialize -q
  .. [100%]
  2 passed in 1.03s
  ```

The fresh scoped CPU/Ruff verification is recorded in the final handoff. Full-regression,
live-service, and paid acceptance remain owned by the main agent.

## Behavioral notes and remaining validation owner

- Scene output is only a priority hint. Only a fully assessed required-event group whose every
  frame is `outside`/`unassessable` is delayed. `unknown`, partial/mixed groups, and forced review
  stay eligible. Audit frames are never treated as reviewed by a preview.
- `candidate_count` covers the original unique candidate universe; `pool_frames` is capped at 64
  and `2 * budget`; `review_frames` is a pool subset; deferred/unreviewed counts are computed from
  the actual final frame IDs.
- Each `video_scene:<batch>` checkpoint is bound to the run ID, source asset, ordered frame
  indices and derived frame asset IDs, selection/prompt/model/API pins. Cached batches are
  validated before reuse and a context mismatch is non-retryable `configuration_changed`.
- The main agent owns full backend regression, live API/GPU acceptance, long-video run, frontend
  integration, documentation/spec synchronization, and VCS checkpoint/rollback.

## Targeted recovery correction

An independent review found that `prepare_video` compared mutable replacement-worker VLM
settings before `classify_scene_batches` could read a fully saved `video_scene:0` checkpoint.
This blocked recovery when every scene batch was durable but the final `video_evidence`
checkpoint was missing.

RED was reproduced by deleting only `video_evidence`, changing the replacement worker's
`api_timeout_seconds` by one, and rerunning `prepare_video`. It failed non-retryably with
`configuration_changed` at the activity-level VLM check even though the selector batch was
already saved.

The minimal fix makes selector acquisition lazy. For each batch, the helper first validates a
cached checkpoint against the original run, source asset, ordered frame IDs/assets, and saved
pins. Only when that batch is absent does it call back into `VideoActivities` to compare mutable
VLM settings, publish `video_scene`, and construct/reuse the client immediately before the new
request. A paired test deletes both `video_evidence` and `video_scene:0` and proves the changed
worker is still rejected without invoking the selector. Version checks remain before selection;
cancellation and DB exceptions remain uncaught.

Fresh correction verification:

```text
.venv/bin/python -m pytest \
  backend/tests/test_video_selection.py \
  backend/tests/test_video_selection_activity.py \
  backend/tests/test_video_policy.py \
  backend/tests/test_inspection_start.py -q
...................................................... [100%]
54 passed in 1.03s

.venv/bin/ruff check \
  backend/src/pipe_inspector/workflow/video_activities.py \
  backend/src/pipe_inspector/workflow/video_selection.py \
  backend/tests/test_video_selection_activity.py
All checks passed!
```

The already-running paid long-video activity loaded the earlier worker implementation and was
not restarted; this correction changes only final-checkpoint recovery after cached scene batches.
