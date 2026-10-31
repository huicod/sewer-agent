#!/bin/sh
# Run with the matching temporalio/admin-tools image. The PostgreSQL bootstrap
# already created both databases; this role neither creates databases nor owns the app DB.
set -eu
case "${1:-}" in
    schema)
        : "${SQL_PASSWORD:?Temporal database password is required}"
        schema_root=${TEMPORAL_SCHEMA_ROOT:-/etc/temporal/schema/postgresql/v12}
        for db in temporal temporal_visibility; do
            case "$db" in temporal) kind=temporal ;; *) kind=visibility ;; esac
            set -- --plugin postgres12 --ep "${POSTGRES_SEEDS:-postgres}" \
                -p "${DB_PORT:-5432}" -u sewer_temporal --db "$db"
            temporal-sql-tool "$@" setup-schema -v 0.0
            temporal-sql-tool "$@" update-schema -d "$schema_root/$kind/versioned"
        done
        ;;
    namespace)
        # A listening socket does not mean the frontend's namespace service is ready yet.
        attempts=0
        until temporal operator namespace describe --namespace default >/dev/null 2>&1; do
            if temporal operator cluster health >/dev/null 2>&1; then
                temporal operator namespace create --namespace default --retention 72h || :
            fi
            attempts=$((attempts + 1))
            if [ "$attempts" -ge 30 ]; then
                echo 'Temporal namespace did not become ready' >&2
                exit 1
            fi
            sleep 2
        done
        ;;
    *) echo 'expected schema or namespace' >&2; exit 2 ;;
esac
