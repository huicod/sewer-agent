# Conservative guided-review design

## Boundary

The existing `GuidedImageReviewer` remains the single VLM boundary. This
change is limited to the deterministic fusion contract and its versioned
replay path. It does not retrain YOLO, change the Qwen API client, or add a
second VLM call.

## Policy model

`fuse_guided(observation, review, policy=...)` will support two explicit
policies:

- `guided-conservative-v1` (default): raw detector boxes are the recall floor.
  `accept` can confirm a box; every rejection/revision is advisory and keeps
  the raw box pending; every valid additional anomaly is pending advice.
- `guided-confidence-v2` (legacy): the previously shipped experimental
  delete/revise/add behavior, available only for historical replay and
  comparison.

The returned `changes` record remains the audit contract. New pending gates
state why the VLM proposal was not promoted. The returned `predictions` field
is the conservative candidate set used for an automated detector baseline;
pending additions remain in `changes` and the online `Finding` queue.

## Compatibility and persistence

The new policy gets a new version pin. Existing cached runs with the legacy pin
must either explicitly request the legacy policy or be rejected as a policy
mismatch. This prevents an old gallery from being silently scored under new
semantics.

## Testing strategy

First add unit tests for the new default behavior and run them red. Then add the
minimal policy implementation and update the existing legacy-behavior tests to
call the explicit legacy policy. Finally run the focused guided-review/policy,
workflow, evaluation, and incremental-cache tests. Recompute the cached
606-image fusion offline from persisted review JSON only; do not call the VLM.
