from a13n_service.database.default_comparison import compare_server_default
from a13n_service.database.metadata import service_metadata
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

ENVIRONMENT_TABLES = {
    "run_environment_mounts",
    "environment_providers",
    "environment_templates",
    "environments",
    "environment_commands",
    "environment_template_revisions",
}


def _assert_tables(config: PostgreSQLConfig, *, present: bool) -> None:
    engine = create_engine(database_url(config))
    try:
        tables = set(inspect(engine).get_table_names())
        if present:
            assert ENVIRONMENT_TABLES <= tables
            columns = {column["name"]: column for column in inspect(engine).get_columns("environments")}
            assert "expires_at" in columns
            assert not columns["name"]["nullable"]
            provider_columns = {column["name"] for column in inspect(engine).get_columns("environment_providers")}
            assert "configuration_source" in provider_columns
            assert "ix_environments_capacity" in {
                index["name"] for index in inspect(engine).get_indexes("environments")
            }
            with engine.connect() as connection:
                context = MigrationContext.configure(
                    connection,
                    opts={
                        "compare_server_default": compare_server_default,
                        "include_object": lambda _object, name, kind, _reflected, _comparison: (
                            name in ENVIRONMENT_TABLES if kind == "table" else True
                        ),
                    },
                )
                assert compare_metadata(context, service_metadata()) == []
            revision_columns = {
                column["name"] for column in inspect(engine).get_columns("environment_template_revisions")
            }
            assert {"template_config", "template_id", "provider_id"} <= revision_columns
            assert "provider" not in revision_columns
            conditions = {c["name"]: c["sqltext"] for c in inspect(engine).get_check_constraints("environments")}
            condition = conditions["ck_environments_retention_condition_valid"]
            assert "active" in condition and "idle" in condition and "waiting_approval" not in condition
        else:
            assert ENVIRONMENT_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_tables(config, present=True)
    migrator.downgrade("base")
    _assert_tables(config, present=False)


def test_environment_schema_migrates_up_and_down(postgres_database: PostgreSQLConfig) -> None:
    _exercise_migration(postgres_database)
