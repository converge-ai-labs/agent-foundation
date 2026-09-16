"""Fixed Bot-free test composition, run in its own Python process."""

# ruff: noqa: E402
# Imports intentionally follow the cold-process application blocker.
import importlib.abc
import sys
from pathlib import Path


class BlockApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "a13n_service.bots" or fullname.startswith("a13n_service.bots."):
            raise ImportError(f"Application import forbidden in core composition: {fullname}")
        return None


sys.meta_path.insert(0, BlockApplication())

from a13n_service.connectivity.accounts.domain import Account
from a13n_service.database import migration
from a13n_service.database.metadata import core_metadata
from a13n_service.interactions.domain import Run

assert "bot_memory" not in Run.model_fields
assert "memory" not in Account.model_fields
assert not any(name.startswith("bot_") for name in core_metadata().tables)


class CoreMigrator(migration.DatabaseMigrator):
    def __init__(self, database, migration_config=None):
        super().__init__(
            database,
            migration_config,
            script_location=Path(__file__).with_name("core_migrations"),
            metadata=core_metadata,
        )


migration.DatabaseMigrator = CoreMigrator

if __name__ == "__main__":
    if sys.argv[1:] == ["--schema-only"]:
        import os

        from a13n_service.database.default_comparison import compare_server_default
        from a13n_service.storage.config import PostgreSQLConfig
        from a13n_service.storage.relational import database_url
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from sqlalchemy import create_engine, inspect, text

        config = PostgreSQLConfig(url=os.environ["A13N_CORE_TEST_DATABASE_URL"])
        CoreMigrator(config).upgrade()
        engine = create_engine(database_url(config))
        try:
            with engine.connect() as connection:
                assert not any(name.startswith("bot_") for name in inspect(connection).get_table_names())
                context = MigrationContext.configure(
                    connection, opts={"compare_server_default": compare_server_default}
                )
                differences = compare_metadata(context, core_metadata())
                assert differences == [], differences
                triggers = set(
                    connection.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")).scalars()
                )
                assert triggers == {
                    "reject_sealed_run_update",
                    "reject_terminal_run_attempt_update",
                    "reject_lifecycle_fact_update",
                    "validate_hook_subscription_revision_insert",
                    "reject_hook_subscription_revision_update",
                    "validate_inline_hook_revision",
                    "preserve_inline_hook_head",
                }
        finally:
            engine.dispose()
    else:
        import pytest

        raise SystemExit(pytest.main(sys.argv[1:]))
