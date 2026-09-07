"""Thin Alembic environment using a runner-owned SQLite connection."""

from typing import Any, Literal

from alembic import context
from sqlalchemy import Connection

from a13n_harness_ui.storage.metadata import harness_ui_metadata
from a13n_harness_ui.storage.utc_datetime import UtcDateTime

config = context.config
target_metadata = harness_ui_metadata()


def render_item(kind: str, value: Any, autogen_context: Any) -> str | Literal[False]:
    del autogen_context
    if kind == "type" and isinstance(value, UtcDateTime):
        return "sa.DateTime()"
    return False


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("Harness UI migrations require a runner-owned SQLAlchemy connection")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
        render_item=render_item,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Harness UI migrations require a runner-owned SQLite connection")
run_migrations_online()
