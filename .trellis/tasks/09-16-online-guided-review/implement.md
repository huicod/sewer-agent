# Implementation and evidence

1. Write failing policy/online wiring tests; preserve offline fusion behavior while sharing it.
2. Wire new default, version dispatch, persistence, parsing and optional-tool baselines; test legacy pins and replay.
3. Compare saved 48-image observations/reviews with original saved predictions/changes, no model calls.
4. Run backend tests/lint, one independent scoped review, inspect complete diff and credential exclusions.
5. Save task/journal evidence, commit via jj and push independent branch. Verify remote refs and paused services.

Rollback: deploy unchanged main/v0.1.0-prototype; do not rewrite existing task pins. Existing legacy modes remain explicitly configurable.
