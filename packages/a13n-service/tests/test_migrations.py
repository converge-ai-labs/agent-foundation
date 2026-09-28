"""The generated revisions build exactly the schema the tests use: tables, rules and all."""

from a13n_service.distribution import OSS
from a13n_service.migrations.runner import check, migration_connection, upgrade
from a13n_service.settings import Database
from alembic import command
from sqlalchemy import create_engine, text

RULES = """
SELECT 'function', p.proname, regexp_replace(pg_get_functiondef(p.oid), '\\s+', ' ', 'g')
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'trigger', t.tgname || ' ON ' || t.tgrelid::regclass::text, pg_get_triggerdef(t.oid)
FROM pg_trigger t WHERE NOT t.tgisinternal
UNION ALL
SELECT 'check', c.conname || ' ON ' || c.conrelid::regclass::text, pg_get_constraintdef(c.oid)
FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'public' AND c.contype = 'c'
ORDER BY 1, 2
"""

# Everything a revision can leave in the schema besides its CHECKs, which go with their tables.
OBJECTS = """
SELECT 'relation', c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
UNION ALL
SELECT 'function', p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'
UNION ALL
SELECT 'trigger', t.tgname FROM pg_trigger t WHERE NOT t.tgisinternal
UNION ALL
SELECT 'type', t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd', 'r', 'm')
ORDER BY 1, 2
"""


def rows(database: Database, query: str) -> list[tuple[str, ...]]:
    engine = create_engine(database.url.get_secret_value())
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(query))]
    finally:
        engine.dispose()


def test_revisions_match_metadata_and_rules(empty_database: Database, database: Database) -> None:
    upgrade(empty_database, OSS)
    # Alembic compares tables, columns, indexes and keys; rules and CHECK expressions are compared directly.
    check(empty_database, OSS)
    migrated = rows(empty_database, RULES)
    assert migrated == rows(database, RULES)
    assert len(migrated) >= len(OSS.rules())


def test_revisions_downgrade_to_an_empty_schema_and_upgrade_again(empty_database: Database) -> None:
    upgrade(empty_database, OSS)
    with migration_connection(empty_database, OSS) as config:
        command.downgrade(config, "base")
    # Only Alembic's own version table survives.
    assert rows(empty_database, OBJECTS) == [("relation", "alembic_version")]
    upgrade(empty_database, OSS)
    check(empty_database, OSS)
