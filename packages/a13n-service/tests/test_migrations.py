"""The generated revisions build exactly the schema the tests use: tables, rules and all."""

from a13n_service.distribution import OSS
from a13n_service.migrations.runner import check, upgrade
from a13n_service.settings import Database
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


def rules(database: Database) -> list[tuple[str, str, str]]:
    engine = create_engine(database.url.get_secret_value())
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(RULES))]
    finally:
        engine.dispose()


def test_revisions_match_metadata_and_rules(empty_database: Database, database: Database) -> None:
    upgrade(empty_database, OSS)
    # Alembic compares tables, columns, indexes and keys; rules and CHECK expressions are compared directly.
    check(empty_database, OSS)
    migrated = rules(empty_database)
    assert migrated == rules(database)
    assert len(migrated) >= len(OSS.rules())
