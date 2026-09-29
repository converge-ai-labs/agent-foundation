"""Alembic runs only with the caller's explicit composed metadata, table rules and connection.

Autogenerate cannot see triggers or functions, so a generated revision appends the rules of every table it
creates (and the shared functions, in the first revision) as SQL rendered verbatim. It also renders
`use_alter` foreign keys, which break create-order cycles such as threads and runs, as separate ALTERs:
`CREATE TABLE` skips them, so leaving them inline would silently drop them.
"""

import re
from typing import Any

from alembic import context
from alembic.autogenerate import renderers
from alembic.operations import ops
from sqlalchemy import ForeignKeyConstraint

from a13n_service.infra.db import FUNCTIONS

_FUNCTION = re.compile(r"CREATE FUNCTION (\w+)\(")


# env.py runs once per command in the same process, so the renderer is replaced, not added.
@renderers.dispatch_for(ops.ExecuteSQLOp, replace=True)
def _render_sql(_: Any, op: ops.ExecuteSQLOp) -> str:
    sql = str(op.sqltext).strip()
    if '"""' in sql:
        raise ValueError("Rule SQL cannot contain triple quotes")
    return f'op.execute(\n    """\n{sql}\n"""\n)'


def _deferred_foreign_keys(upgrade: ops.UpgradeOps) -> list[ops.CreateForeignKeyOp]:
    deferred = []
    for op in upgrade.ops:
        if isinstance(op, ops.CreateTableOp):
            alter = [item for item in op.columns if isinstance(item, ForeignKeyConstraint) and item.use_alter]
            # Identity, not equality: `==` on SQL elements builds an expression.
            op.columns = [item for item in op.columns if all(item is not constraint for constraint in alter)]
            deferred += [ops.CreateForeignKeyOp.from_constraint(constraint) for constraint in alter]
    # A table's constraints are a set; sorting keeps regenerated revisions stable.
    return sorted(deferred, key=lambda op: (op.source_table, str(op.constraint_name)))


def _complete_revision(migration: Any, revision: Any, directives: list[Any]) -> None:
    script = directives[0]
    tables = [op for op in script.upgrade_ops.ops if isinstance(op, ops.CreateTableOp)]
    if not tables:
        return
    for op in tables:
        op.info = {}  # The rules carried in table info render below as SQL; the info itself is dead weight.
    created = [op.table_name for op in tables]
    deferred = _deferred_foreign_keys(script.upgrade_ops)
    script.upgrade_ops.ops.extend(deferred)
    # Break the same cycles before dropping tables on downgrade.
    script.downgrade_ops.ops[:0] = [op.reverse() for op in reversed(deferred)]
    rules: dict[str, list[str]] = context.config.attributes["rules"]
    statements = list(FUNCTIONS) if migration.get_current_revision() is None else []
    statements += [statement for table in created for statement in rules.get(table, ())]
    script.upgrade_ops.ops.extend(ops.ExecuteSQLOp(statement) for statement in statements)
    # Tables drop with their triggers; functions must go too, or a later upgrade cannot recreate them.
    functions = [name for statement in statements for name in _FUNCTION.findall(statement)]
    script.downgrade_ops.ops.extend(ops.ExecuteSQLOp(f"DROP FUNCTION {name}() CASCADE") for name in reversed(functions))


context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=context.config.attributes["metadata"],
    compare_type=True,
    compare_server_default=True,
    process_revision_directives=_complete_revision,
)
with context.begin_transaction():
    context.run_migrations()
