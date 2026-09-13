# Task 1 implementation report

## Scope

- Added the test-only admission, activity timing, and measurement summary helper in
  `backend/tests/video_cost_live.py`.
- Added offline behavior tests in `backend/tests/test_video_cost_measurements.py`.
- Added the default-gated real-stack acceptance in
  `backend/tests/integration/test_video_cost_live.py`.
- No production module, environment file, service, schema, Git, jj, strategy, model,
  prompt, or production semantic budget was changed by this implementation agent.

The integration test preserves `video_run_config`'s semantic review budget of 32 and
human-review version. It reuses `LiveExecutionRecorder`, `GpuExecutionRecorder`, and
the measured detector assembled by `browser_live.build_live_components`; the separate
physical provider request cap remains 8 and includes retried calls. Test-only activity
overrides retain the registered `scan_video`, `prepare_video`, `verify_video_frame`,
and `finish_video` names.

Admission requires both exact opt-in gates, an existing absolute video, and a new
absolute output strictly below repository `var/evaluations`. Existing media inspection
rejects invalid or over-60-second sources before adapter construction. The live test
uses its own schema, CPU/GPU queues, asset root, and exact workflow ID. It observes the
saved final checkpoint plus actual `human_review` stage, or a terminal workflow,
writes metrics and linked raw evidence frames first, then terminates only its own
still-running workflow and drops only its schema. It never submits a human decision.

`video-cost-summary.json` records decoded/processed frames, chunk and detector batch
counts, decode status, event intervals, selected/evidence frames, each frame's stored
observation and matching `video_verify:<index>` checkpoint, run pins, final task and
checkpoint, tool attempts, per-activity timings, GPU peaks, safe per-request provider
records, returned token sums, unknown usage, and budget rejections. Currency billing is
left null and explicitly separate. The test prints flushed stage, processed-frame, and
physical-request progress every five seconds during observation.

## TDD evidence

RED, before the helper existed:

```text
$ .venv/bin/python -m pytest backend/tests/test_video_cost_live.py -q
ERROR backend/tests/test_video_cost_live.py
ModuleNotFoundError: No module named 'video_cost_live'
1 error in 0.14s
```

The offline file was subsequently renamed to `test_video_cost_measurements.py` so the
full suite can collect it together with the mandated integration filename without a
pytest module-name collision.

GREEN after implementation and the rename:

```text
$ .venv/bin/python -m pytest backend/tests/test_video_cost_measurements.py backend/tests/integration/test_video_cost_live.py -q
.....s
5 passed, 1 skipped in 0.70s
```

The skip is the required default behavior with the paid/GPU gates absent.

## Regression and lint evidence

```text
$ .venv/bin/python -m pytest backend/tests/test_browser_live.py backend/tests/test_video_policy.py -q
..........................
26 passed in 0.79s

$ .venv/bin/ruff check backend/tests/video_cost_live.py backend/tests/test_video_cost_measurements.py backend/tests/integration/test_video_cost_live.py
All checks passed!
```

The implementation agent did not run a real API/GPU acceptance or start services, as
required by the task contract; the main agent owns that opt-in run and artifact review.
The full backend suite is also left to the main agent's single final pass.

## Scoped review fix: explicit API output bound

The reusable live entry now requires `api_max_output_tokens` to be explicitly
configured before schema creation, adapter construction, or provider work. The guard
does not supply or change a default: it returns the same settings object and preserves
its configured value. `Settings` continues to own the existing 1–8192 validation.

The offline regression uses a local `SimpleNamespace`; it does not instantiate
`Settings`, read `.env`, or modify process environment.

RED before the guard existed:

```text
$ .venv/bin/python -m pytest backend/tests/test_video_cost_measurements.py -q
ERROR backend/tests/test_video_cost_measurements.py
ImportError: cannot import name 'require_bounded_api_output' from 'video_cost_live'
1 error in 0.72s
```

GREEN and default-gated integration collection:

```text
$ .venv/bin/python -m pytest backend/tests/test_video_cost_measurements.py backend/tests/integration/test_video_cost_live.py -q
......s
6 passed, 1 skipped in 0.73s

$ .venv/bin/ruff check backend/tests/video_cost_live.py backend/tests/test_video_cost_measurements.py backend/tests/integration/test_video_cost_live.py
All checks passed!
```

No paid/GPU rerun was performed for this preflight-only correction; the main agent
verified that both already-saved actual runs used the explicit value 4096.
