"""Thin Alembic environment using a runner-owned SQLite connection."""

from alembic import context
from sqlalchemy import Connection

from a13n_ui.storage.metadata import agent_ui_metadata

config = context.config
target_metadata = agent_ui_metadata()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("Agent UI migrations require a runner-owned SQLAlchemy connection")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Agent UI migrations require a runner-owned SQLite connection")
run_migrations_online()
