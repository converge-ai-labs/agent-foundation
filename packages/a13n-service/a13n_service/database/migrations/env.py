"""Thin Alembic environment using a runner-owned database connection."""

from alembic import context
from sqlalchemy import Connection

from a13n_service.database.default_comparison import compare_server_default
from a13n_service.database.metadata import service_metadata

config = context.config
target_metadata = service_metadata()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("online migrations require a runner-owned SQLAlchemy connection")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=compare_server_default,
        render_as_batch=connection.dialect.name == "sqlite",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("a13n Service migrations require a runner-owned database connection")
run_migrations_online()
