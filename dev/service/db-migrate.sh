#!/usr/bin/env bash
# Generate a a13n-service Alembic revision from a disposable database.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || -z "$1" ]]; then
    echo 'Usage: make db-migrate msg="describe the schema change"' >&2
    exit 2
fi

MESSAGE="$1"
MIGRATION_COMMAND=(uv run --locked a13n-service db)
if [[ "${2:-oss}" == "core-verification" ]]; then
    MIGRATION_COMMAND=(uv run --locked python dev/service/core-schema.py)
elif [[ "${2:-oss}" != "oss" ]]; then
    echo "Unknown migration generation composition" >&2
    exit 2
fi
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose --env-file /dev/null --project-name "a13n-migrate-${$}" -f "$ROOT_DIR/dev/service/compose.yaml")
DATABASE_NAME="a13n_service_migrate_${$}_${RANDOM}"
POSTGRES_PORT=""

cleanup() {
    if [[ -n "$POSTGRES_PORT" ]]; then
        "${COMPOSE[@]}" exec -T postgres \
            psql -v ON_ERROR_STOP=1 -U a13n_service_dev -d postgres \
            -c "DROP DATABASE IF EXISTS \"$DATABASE_NAME\" WITH (FORCE);" >/dev/null || true
    fi
    "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null || true
}
trap cleanup EXIT
export A13N_DEV_POSTGRES_PORT=0

"${COMPOSE[@]}" up -d --wait postgres
POSTGRES_PORT="$("${COMPOSE[@]}" port postgres 5432 | awk -F: 'NR == 1 {print $NF}')"
if [[ -z "$POSTGRES_PORT" ]]; then
    echo "Could not resolve the local PostgreSQL port." >&2
    exit 1
fi

"${COMPOSE[@]}" exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U a13n_service_dev -d postgres \
    -c "CREATE DATABASE \"$DATABASE_NAME\";" >/dev/null

DATABASE_URL="postgresql+psycopg://a13n_service_dev:local-only-password@127.0.0.1:${POSTGRES_PORT}/${DATABASE_NAME}"
VERSIONS_DIR="$ROOT_DIR/packages/a13n-service/a13n_service/database/migrations/versions"

if [[ "${2:-oss}" == "core-verification" ]]; then
    VERSIONS_DIR="$ROOT_DIR/packages/a13n-service/tests/database/core_migrations/versions"
fi

echo "Replaying migration history in disposable database $DATABASE_NAME..."
A13N_SERVICE_DATABASE_URL="$DATABASE_URL" \
    "${MIGRATION_COMMAND[@]}" upgrade

echo "Generating migration: $MESSAGE"
A13N_SERVICE_DATABASE_URL="$DATABASE_URL" \
    "${MIGRATION_COMMAND[@]}" migrate "$MESSAGE"

uv run --locked ruff format "$VERSIONS_DIR"
uv run --locked ruff check --fix "$VERSIONS_DIR"

echo "Generated migration in $VERSIONS_DIR."
