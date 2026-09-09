# T02 native persistence report

Date: 2026-09-09 UTC

## Implemented

- `scripts/runtime.py init|start|stop|status` manages one native Temporal process and PostgreSQL 14/main without Docker or systemd.
- PostgreSQL data stays in the package-created host cluster at `/var/lib/postgresql/14/main`; `/root` remains mode 0700.
- `sewer_temporal` owns `temporal` and `temporal_visibility`; `sewer_agent` owns the application DB `sewer_agent`. Both login roles were verified `NOSUPERUSER NOCREATEDB NOCREATEROLE`.
- Temporal Server and schema tool are official `v1.31.2` binaries under `.tools/temporal/`. Missing matching PostgreSQL v12 schema files are fetched from the official tagged source archive only.
- Schema setup uses `SQL_PASSWORD` in the child environment; role SQL/passwords travel to `psql` through stdin, never argv or output.
- Generated secrets are reused from ignored `var/runtime/credentials.env`. The root `.env` receives only a replaceable managed block containing `SEWER_DATABASE_DSN` and Temporal endpoint/namespace; all prior user text is retained.
- Runtime directory mode is 0700; credentials, generated Temporal config, log, PID and `.env` are 0600.
- Temporal PostgreSQL, membership, frontend and all internal RPC listeners bind to loopback. Generated config has four history shards and no archival or metrics listener on port 8000.
- `stop` validates both `/proc/<pid>/exe` and the exact `--config-file` argument before signaling. It never uses broad `pkill` and never stops PostgreSQL.
- `start` returns only after SDK health succeeds and ensures namespace `default` exists. PID 1 is a custom bash init and `systemctl` is offline, so this M0 controller deliberately provides no supervisor platform.

## TDD and static verification

RED:

```text
.venv/bin/python -m pytest backend/tests/test_runtime.py -q
4 expected setup errors: scripts/runtime.py is not implemented
```

GREEN:

```text
.venv/bin/python -m pytest backend/tests/test_runtime.py -q
4 passed in 0.11s
.venv/bin/ruff check scripts/runtime.py backend/tests/test_runtime.py
All checks passed!
```

The fixture tests cover loopback-only/no-optional-service config, stable generated credentials, preservation plus 0600 modes, refusal to signal a mismatched PID, and termination of an exact controlled child.

## Real setup evidence

```text
.venv/bin/python scripts/runtime.py init
initialized=true, postgres=running
postgres_data_dir=/var/lib/postgresql/14/main

.venv/bin/python scripts/runtime.py start
postgres.running=true
temporal: pid=40330, running=true, ready=true, address=127.0.0.1:7233
```

The database ownership and role-privilege checks above were run as PostgreSQL admin without exposing credentials. Config/credential/environment permission checks returned 0600 and runtime directory 0700.

## Actual restart recovery

The root integration owner ran the task-owned real worker/probe and reported:

```text
4 passed in 7.23s
Temporal PID: 40330 -> 41469
sequence: kill test worker -> runtime.py stop -> restart PG 14/main ->
          runtime.py start -> restart worker
before signal: workflow RUNNING, stage=waiting_review
after signal: workflow completed
checkpoint rows: detected=1, completed=1
```

This proves the waiting workflow/history survived both Temporal and PostgreSQL service restarts on this host. It does not provide high availability or protection from host/disk loss; PostgreSQL backup remains separate operational work.
