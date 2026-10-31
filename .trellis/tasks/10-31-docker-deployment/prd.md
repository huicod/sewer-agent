# Portable Docker deployment and model delivery

User authorized implementation, verification, and GitHub synchronization on 2026-10-31, plus uploading the online model bundle to their Hugging Face account once authenticated and identified.

- Provide a fresh Linux NVIDIA deployment with web/API, CPU worker, GPU worker, PostgreSQL and Temporal; initialize storage automatically and persist database/media across recreation.
- Users supply API configuration and the seven online models; provide configuration generation, model validation/preparation, and practical startup/backup/restore instructions.
- Keep existing native services, database, media, model files, and model behavior unchanged. Never commit credentials or runtime data. Default web publication stays on host loopback.
- Pin known-compatible Python/Torch/CUDA and service versions. Build frontend inside the image. Share identical media/model paths across API and workers.
- Validate with unit/static checks and isolated real service tests where available. Report Docker/GPU or provider checks that cannot execute in this nested container. Do not claim readiness from synthetic model outputs.
- HF upload: exactly seven online models plus required metadata; explicit target account, authenticated client, private by default, preserve per-model origin/license statements. No automatic public publication.

Rollback anchor: eec559b9 (dev/agent-v1). Trellis owns task state; jj owns changes. No production data migration is part of this task.
