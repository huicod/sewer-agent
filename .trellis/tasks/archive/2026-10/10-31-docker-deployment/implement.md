# Work allocation and acceptance

1. Main agent: Dockerfile, compose.yaml, .dockerignore, configuration generator, deployment documentation, integration and GitHub push.
2. Container helper agent: deploy/docker/entrypoint.py, deploy/docker/init-postgres.sh, corresponding backend unit tests. No edits outside this scope; no service mutation.
3. Model agent: deploy/models.json, scripts/model_bundle.py and its tests; no HF upload until target/auth confirmed, no source model mutation.
4. Main agent reviews integrated diff; independent read-only review before delivery. All mutations to jj/Trellis by main agent only.

Checks: helper tests, model completeness on actual files, dependency resolution, frontend build (reuse unchanged result), Compose render, SQL initialization/idempotence against isolated local PostgreSQL, image/service checks where environment supports Docker. Preserve running native service identities. Record successful and blocked checks separately. Push existing dev/agent-v1 after local checks.
