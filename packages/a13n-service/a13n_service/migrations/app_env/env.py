"""Host-owned Alembic environment for one App's independent public-schema history."""

from typing import Any

from a13n_service.app_database import AppDatabase
from alembic import context
from alembic.operations import ops
from sqlalchemy import ForeignKeyConstraint

config = context.config
database: AppDatabase = config.attributes["app_database"]


def include_name(name: str | None, type_: str, parents: Any) -> bool:
    # Reflect the entire owned prefix, including dropped models. Native references are read-only.
    return type_ != "table" or (name is not None and database.owns(name) and name != database.version_table)


def include_object(object_: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    return type_ != "table" or (name is not None and database.owns(name))


def complete_revision(migration: Any, revision: Any, directives: list[Any]) -> None:
    script = directives[0]
    deferred = []
    for operation in script.upgrade_ops.ops:
        if isinstance(operation, ops.CreateTableOp):
            operation.info = {}
            alter = [item for item in operation.columns if isinstance(item, ForeignKeyConstraint) and item.use_alter]
            operation.columns = [
                item for item in operation.columns if all(item is not constraint for constraint in alter)
            ]
            deferred.extend(ops.CreateForeignKeyOp.from_constraint(constraint) for constraint in alter)
    deferred.sort(key=lambda operation: (operation.source_table, str(operation.constraint_name)))
    script.upgrade_ops.ops.extend(deferred)
    script.downgrade_ops.ops[:0] = [operation.reverse() for operation in reversed(deferred)]


context.configure(
    connection=config.attributes["connection"],
    target_metadata=database.metadata,
    version_table=database.version_table,
    compare_type=True,
    compare_server_default=True,
    include_name=include_name,
    include_object=include_object,
    process_revision_directives=complete_revision,
)
with context.begin_transaction():
    context.run_migrations()
