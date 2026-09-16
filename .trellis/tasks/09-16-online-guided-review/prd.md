# Online guided image review

User approved aligning online images to the accepted September 11 confidence-tier offline experiment and pushing an independent GitHub branch.

- New image runs always review the original plus up to eight native context crops using the existing guided prompt.
- Reuse exact guided-confidence-v2 selection: protect detector scores >= 0.85, apply clear low-confidence rejection/revision, admit clear nonduplicate five-class additions. OTHER/conflicts/uncertainty require review.
- Keep rejected/advisory evidence auditable; no fabricated VLM confidence. Quality and whole-image audit still gate normal.
- Preserve old pinned runs/checkpoints and optional enhancement/geometry baselines. Video stays unchanged.
- Tests and cached 48-image parity, no paid model calls, no new model training/dependencies, no restarting paused application services.
- Push fix/online-guided-review only; retain main and v0.1.0-prototype; exclude credentials/media/model artifacts.
