# T04 storage subtask report

## Result

- Added immutable intake contracts for sessions, submissions, persisted task records, and grouped session detail.
- Added a plain-psycopg repository with short transactions and three schema-qualified tables: `sewer_sessions`, `sewer_assets`, and `sewer_tasks`.
- `request_id` is the idempotency key; `snapshot.task_id` is the lookup identifier. Identical replay returns the stored record, while any differing replay returns the fixed safe conflict.
- Existing request replay is checked before validating references, so altered/missing references cannot turn a conflict into a misleading not-found response.
- Session, asset-version, asset-parent, and task-input references are database foreign keys. Asset versions are immutable.
- New task projections are UTC `queued` / `pending` / `queued` with zero progress.
- Initial task creation accepts only parentless original image/video assets; derived and parented assets are rejected.
- The optional repository schema is always composed through `psycopg.sql.Identifier`; production defaults to `public`, while tests create and drop only a unique owned schema.
- Connections use a five-second libpq `connect_timeout`. The schema initializer reports a fixed failure without exposing a database exception or DSN.

## TDD evidence

- RED contracts: `ModuleNotFoundError: pipe_inspector.contracts.intake`.
- RED repository: `ModuleNotFoundError: pipe_inspector.storage`.
- RED replay regression: reused `request_id` plus altered missing references raised `MissingRecord` before the replay check.
- GREEN: `SEWER_RUN_STORAGE_TEST=1 .venv/bin/python -m pytest backend/tests/test_intake_contracts.py backend/tests/integration/test_storage.py -q` -> `13 passed in 1.74s`.
- PostgreSQL coverage includes idempotent schema init, reconnect persistence, foreign references, missing records, immutable assets, session grouping, initial-asset validation, and six simultaneous submissions with one `request_id` producing one task.
- Scoped Ruff: all assigned Python files -> `All checks passed!`.
- Real connection inspection returned libpq `connect_timeout` value `5`; no connection parameters or secrets were printed.
- Root separately reported the current HTTP/PostgreSQL intake integration `2 passed`, unit guard `5 passed`, and successful public-table initialization. Per instruction, this worker did not duplicate the full backend suite.

## Scope and concerns

- Modified only the seven assigned files. No dependency, service, credential, or Temporal state was changed; no Git/jj command or history mutation was performed.
- `initialize()` deliberately creates tables only; callers/tests own schema creation. This prevents an application role from implicitly provisioning arbitrary schemas.
- This is the approved initial schema path, not a general migration framework; later schema evolution needs its own explicit task.
