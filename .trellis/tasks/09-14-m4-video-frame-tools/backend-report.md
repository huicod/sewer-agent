# Backend report

## Result

Implemented Task 1's selected-video-frame backend path. A selected `FRAME` remains rejected by the ordinary derived-media entry point and is accepted only through the explicit frame-tool admission API. Admission freezes the real parent video, exact evidence frame/observation, frame-local recall decision, operation pins, and an independent per-parent/frame/operation quota in one transaction.

Public entry point:

```python
async def InspectionStarter.admit_frame_tool(
    parent_task_id: UUID,
    submission: TaskSubmission,
    *,
    enhancement=None,
    geometry=None,
)
```

Exactly one of `enhancement` or `geometry` is required. Geometry accepts an explicit ROI for `segment` / `relative_measure`, or a whole-frame `relative_depth`; a frame `finding_id` is rejected. Request replay returns the already frozen child before reading mutable worker/model configuration.

`TaskRecord.frame_source` is optional and has shape:

```json
{"video_asset": {"asset_id": "...", "version": 1}, "frame_index": 7, "timestamp_seconds": 0.7}
```

The atomic `frame_source` checkpoint has shape:

```json
{
  "pin": {
    "version": "video-frame-tool-v1",
    "parent_task_id": "...",
    "video_asset": {"asset_id": "...", "version": 1},
    "frame_asset": {"asset_id": "...", "version": 1},
    "frame_index": 7,
    "timestamp_seconds": 0.7,
    "width": 32,
    "height": 24,
    "operation": "enhance"
  },
  "observation": {},
  "video_verify": null,
  "decision": {}
}
```

Enhancement/geometry baselines use only that frozen frame-local decision and do not rerun detector or original-frame VLM. First tool-result writes validate the complete frozen source before committing asset/call/checkpoint atomically. Final frame findings require the selected frame asset and exact frame index/timestamp; enhancement-created anomalies receive the same locator. On configuration change, a frame child reuses frozen frame findings rather than copying unrelated findings from the full parent video.

## TDD evidence

- RED: contract/start/storage tests initially failed because `frame_source`, `VideoFrameToolPin`, `admit_frame_tool`, the independent quota table, and the narrow FRAME turn gate did not exist; ordinary admission rejected the selected frame as derived media.
- GREEN: contract, admission, source/pin mismatch, independent quota, turn gate, frozen baseline, and start routing tests pass.
- RED: the final enhancement anomaly test observed `frame_index=None` instead of `7`; the frame configuration-change test could not pass the pinned run to the supplement override.
- GREEN: finalization now validates/fills exact frame locators and frame configuration fallback returns only the frozen decision findings (`3 passed`).
- Related unit verification: `68 passed in 1.26s`.
- Disposable-schema PostgreSQL verification covering frame admission plus original-image enhancement/geometry compatibility: `40 passed in 18.00s`.
- Targeted Ruff check: passed; targeted format check: `18 files already formatted`.

## Files and boundaries

Task-owned changes are confined to the contracts, storage, start/config/activity helpers, and new backend tests listed in Task 1. The authorized necessary expansion is `workflow/activities.py`: only the decision-to-final-checkpoint path was extended to validate/fill locators for an already verified frame child, plus passing the pinned run to configuration-change finding construction. Existing image behavior and enhancement fusion policy are unchanged.

No dependency, schema migration framework, model/service, GPU/API, frontend, report renderer, dispatcher/intent, or VCS action was added or invoked. The new table is created by the existing repository initializer and tests use their own schema.
