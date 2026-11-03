#!/usr/bin/env bash
# Generate a a13n-service Alembic revision from a disposable database.
set -euo pipefail

if [[ $# -ne 1 || -z "$1" ]]; then
    echo 'Usage: make db-migrate msg="describe the schema change"' >&2
    exit 2
fi

MESSAGE="$1"
MIGRATION_COMMAND=(uv run --locked a13n-service migrate)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose --env-file /dev/null --project-name "a13n-migrate-${$}" -f "$ROOT_DIR/dev/service/compose.yaml")
DATABASE_NAME="a13n_service_migrate_${$}_${RANDOM}"
case "${MIGRATION_POSTGRES:-docker}" in
    docker)
        # This Compose project and its volumes belong only to this generator.
        trap '"${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null || true' EXIT
        export A13N_DEV_POSTGRES_PORT=0
        "${COMPOSE[@]}" up -d --wait postgres
        POSTGRES_PORT="$("${COMPOSE[@]}" port postgres 5432 | awk -F: 'NR == 1 {print $NF}')"
        if [[ -z "$POSTGRES_PORT" ]]; then
            echo "Could not resolve the local PostgreSQL port." >&2
            exit 1
        fi
        "${COMPOSE[@]}" exec -T postgres createdb -U a13n_service_dev "$DATABASE_NAME"
        DATABASE_URL="postgresql+psycopg://a13n_service_dev:local-only-password@127.0.0.1:${POSTGRES_PORT}/${DATABASE_NAME}"
        ;;
    native)
        # Never connect to the developer's PostgreSQL. Only this private socket
        # directory can reach the short-lived cluster; no TCP listener is opened.
        MIGRATION_DIR="$(mktemp -d /tmp/a13n-migrate.XXXXXXXX)"
        cleanup_native() {
            if [[ -f "$MIGRATION_DIR/data/postmaster.pid" ]]; then
                pg_ctl -D "$MIGRATION_DIR/data" -m fast -w stop >/dev/null || return
            fi
            rm -rf -- "$MIGRATION_DIR"
        }
        trap cleanup_native EXIT
        initdb -D "$MIGRATION_DIR/data" -U a13n_service_dev --auth=trust --no-locale --encoding=UTF8 --no-sync >/dev/null
        pg_ctl -D "$MIGRATION_DIR/data" -l "$MIGRATION_DIR/postgres.log" \
            -o "-c listen_addresses='' -k '$MIGRATION_DIR' -p 5432" -w start >/dev/null
        createdb -h "$MIGRATION_DIR" -p 5432 -U a13n_service_dev "$DATABASE_NAME"
        DATABASE_URL="postgresql+psycopg://a13n_service_dev@/${DATABASE_NAME}?host=$MIGRATION_DIR&port=5432"
        ;;
    *) echo 'MIGRATION_POSTGRES must be docker or native' >&2; exit 2 ;;
esac
VERSIONS_DIR="$ROOT_DIR/packages/a13n-service/a13n_service/migrations/versions"

echo "Replaying migration history in disposable database $DATABASE_NAME..."
A13N_DATABASE__URL="$DATABASE_URL" \
    env -u A13N_DEV_POSTGRES_PORT "${MIGRATION_COMMAND[@]}"

echo "Generating migration: $MESSAGE"
A13N_DATABASE__URL="$DATABASE_URL" \
    env -u A13N_DEV_POSTGRES_PORT "${MIGRATION_COMMAND[@]}" --generate "$MESSAGE"

uv run --locked ruff format "$VERSIONS_DIR"
uv run --locked ruff check --fix "$VERSIONS_DIR"

echo "Generated migration in $VERSIONS_DIR."
