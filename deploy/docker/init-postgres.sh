#!/bin/sh
# Called by the official PostgreSQL image for a new named data volume only.
set -eu

: "${SEWER_APP_DB_PASSWORD:?SEWER_APP_DB_PASSWORD must be supplied}"
: "${SEWER_TEMPORAL_DB_PASSWORD:?SEWER_TEMPORAL_DB_PASSWORD must be supplied}"
: "${POSTGRES_USER:=postgres}"

# psql variables plus format(%L) quote arbitrary passwords as SQL literals.
# Existing role passwords are deliberately retained: restarting is not rotation.
# Suppress raw server diagnostics, which may include the executed PASSWORD statement.
if ! psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" --dbname postgres \
    --set=app_password="$SEWER_APP_DB_PASSWORD" \
    --set=temporal_password="$SEWER_TEMPORAL_DB_PASSWORD" \
    >/dev/null 2>&1 <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
              'sewer_agent', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sewer_agent')
\gexec
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
              'sewer_temporal', :'temporal_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sewer_temporal')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', 'sewer_agent', 'sewer_agent')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sewer_agent')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', 'temporal', 'sewer_temporal')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'temporal')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', 'temporal_visibility', 'sewer_temporal')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'temporal_visibility')
\gexec
REVOKE ALL ON DATABASE sewer_agent FROM PUBLIC;
REVOKE ALL ON DATABASE temporal FROM PUBLIC;
REVOKE ALL ON DATABASE temporal_visibility FROM PUBLIC;
\connect sewer_agent
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO sewer_agent;
\connect temporal
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO sewer_temporal;
\connect temporal_visibility
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO sewer_temporal;
SQL
then
    echo 'error: PostgreSQL application/Temporal initialization failed' >&2
    exit 1
fi
echo 'application and Temporal databases initialized'
