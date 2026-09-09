# Native Temporal + PostgreSQL persistence probe (M0/T02)
Date: 2026-09-09 UTC. One Ubuntu host/container; no Docker/Kubernetes. This probe installed or started nothing.

## Decision
Use Ubuntu PostgreSQL 14 plus one official Temporal Server `v1.31.2` process running all four default services. PostgreSQL durably stores workflow histories/timers across a Temporal restart. Elasticsearch and UI are unnecessary for M0 and basic SQL visibility.

Do **not** use `temporal server start-dev`, even with `--db-filename`: Temporal classifies it as a development server. File-backed SQLite is only an explicitly disclosed fallback, not the planned PostgreSQL self-hosted service.

## Observed host facts
- Ubuntu 22.04.5 (`jammy`), `x86_64`, root, 83 GiB free.
- No `postgres`, `pg_ctl`, `psql`, `temporal-server`, or `temporal`; ports 5432/7233 free.
- Ubuntu apt candidate: PostgreSQL `14.24-0ubuntu0.22.04.1`. Huawei mirror TLS failed; official Ubuntu and PGDG endpoints answer.
- PID 1 is `/init/boot/boot.sh`; `systemctl` is `offline`. Use `pg_ctlcluster` plus an explicit Temporal process, not systemd.

## Official versions and URLs
- Server `v1.31.2` (latest stable observed): <https://github.com/temporalio/temporal/releases/download/v1.31.2/temporal_1.31.2_linux_amd64.tar.gz>
- Matching config/schema: <https://github.com/temporalio/temporal/archive/refs/tags/v1.31.2.tar.gz>
- CLI `v1.8.3` (latest observed): <https://github.com/temporalio/cli/releases/download/v1.8.3/temporal_cli_1.8.3_linux_amd64.tar.gz>
- PostgreSQL Ubuntu install: <https://www.postgresql.org/download/linux/ubuntu/>
- Temporal binary deployment: <https://docs.temporal.io/self-hosted-guide/deployment>

Project-local extraction from repository root:
```bash
mkdir -p .tools/temporal/bin .tools/temporal/src
SERVER_URL=https://github.com/temporalio/temporal/releases/download/v1.31.2/temporal_1.31.2_linux_amd64.tar.gz
SOURCE_URL=https://github.com/temporalio/temporal/archive/refs/tags/v1.31.2.tar.gz
CLI_URL=https://github.com/temporalio/cli/releases/download/v1.8.3/temporal_cli_1.8.3_linux_amd64.tar.gz
curl -fL "$SERVER_URL" | tar -xz -C .tools/temporal/bin
curl -fL "$SOURCE_URL" | tar -xz -C .tools/temporal/src --strip-components=1 \
  temporal-1.31.2/config/development-postgres12.yaml \
  temporal-1.31.2/config/dynamicconfig/development-sql.yaml \
  temporal-1.31.2/schema/postgresql/v12
curl -fL "$CLI_URL" | tar -xz -C .tools/temporal/bin
```

## PostgreSQL and Temporal schema
After apt is pointed temporarily at an approved official Ubuntu source:
```bash
apt-get update
apt-get install -y postgresql-14 postgresql-client-14
pg_ctlcluster --skip-systemctl-redirect 14 main start
sudo -u postgres createuser --createdb --pwprompt temporal
```

Prompt for the same password in the installing shell (`SQL_PASSWORD` is an official tool environment variable); never commit it:
```bash
read -rsp 'Temporal DB password: ' SQL_PASSWORD; export SQL_PASSWORD
T=.tools/temporal/bin/temporal-sql-tool
$T --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 --db temporal create
$T --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 --db temporal setup-schema -v 0.0
$T --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 --db temporal update-schema -d .tools/temporal/src/schema/postgresql/v12/temporal/versioned
$T --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 --db temporal_visibility create
$T --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 --db temporal_visibility setup-schema -v 0.0
$T --plugin postgres12 --ep 127.0.0.1 -u temporal -p 5432 --db temporal_visibility update-schema -d .tools/temporal/src/schema/postgresql/v12/visibility/versioned
unset SQL_PASSWORD
```

Copy `development-postgres12.yaml` to `.tools/temporal/config/server.yaml`. Edit only both datastore credentials and `dynamicConfigClient.filepath` (absolute path to extracted `development-sql.yaml`). Keep DB endpoints `127.0.0.1:5432`, frontend localhost-only, and secret-bearing config mode `0600`.

## Native start and restart proof
The exact foreground command starts frontend/history/matching/worker:
```bash
.tools/temporal/bin/temporal-server --config-file "$PWD/.tools/temporal/config/server.yaml" --allow-no-auth start
```
Create the namespace once:
```bash
.tools/temporal/bin/temporal --address 127.0.0.1:7233 operator namespace create default
```

Proof: start a real workflow with a durable timer longer than the test; record workflow/run IDs; terminate and relaunch the Temporal command; query that execution; let the timer fire and verify completion. Stronger proof: restart PostgreSQL using `pg_ctlcluster --skip-systemctl-redirect 14 main restart`, relaunch Temporal, and query the same history. A human-wait workflow must remain open and continue only when its signal/update arrives.

## Permissions and limit
Root is needed for apt/PostgreSQL OS cluster. PostgreSQL-admin authority creates the role/databases. Run Temporal unprivileged with config read access and writes only to log/PID paths. This is durable, not highly available; host/disk loss still needs PostgreSQL backup.
