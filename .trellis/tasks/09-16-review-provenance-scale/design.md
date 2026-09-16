# Scoped design

Reuse stored detect/verify checkpoints and task snapshots through a read-only image-evidence projection. Return original observation, typed VLM advice (optional evidence strength), saved fusion changes (no synthetic prediction scores), current findings, input asset and parent task ID. Do not recompute fusion or join findings to advice by assumed list order; users compare candidate indices/boxes with final findings. Supplement tasks use their own copied detect/verify checkpoints; absent evidence remains absent.

Reports embed the same projection; detector score text identifies the original candidate score, not confidence of the final label. The UI expands evidence on demand with task/revision request invalidation and original-pixel overlays. Existing workflows and schemas remain unchanged except an optional report evidence field.

Data preparation groups explicit video-frame suffixes. A separate task-local diagnostic script reconstructs the legacy split with its old grouping before producing a corrected future-training manifest. Both receive explicit provenance labels. Bounded evaluation uses existing weight/threshold and original pixels, compares same inputs, maps tile coordinates, merges duplicates and saves target-level metrics by relative area. No automatic production promotion.
