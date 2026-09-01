"""migrate environment access

Revision ID: 9958168d49de
Revises: 55b7c7d359aa
Create Date: 2026-08-31 09:00:32.431912+00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9958168d49de"
down_revision: str | Sequence[str] | None = "55b7c7d359aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FILE_READ_ACTIONS = frozenset(
    {
        "environment.file.stat",
        "environment.file.read_text",
        "environment.file.read_bytes",
        "environment.file.list",
        "environment.file.query",
        "environment.file.search_text",
        "environment.file.copy_source",
    }
)
_FILE_MUTATION_ACTIONS = frozenset(
    {
        "environment.file.write_text",
        "environment.file.patch_text",
        "environment.file.mkdir",
        "environment.file.move",
        "environment.file.remove",
        "environment.file.write_bytes",
        "environment.file.copy_destination",
    }
)
_STATE_ACTIONS = frozenset({"environment.state.export", "environment.state.restore"})
_NON_FILE_ACTION_PREFIXES = (
    "environment.shell.",
    "environment.process.",
    "environment.output.",
    "environment.port.",
)
_ALL_ACTIONS = (
    _FILE_READ_ACTIONS
    | _FILE_MUTATION_ACTIONS
    | _STATE_ACTIONS
    | frozenset(
        {
            "environment.shell.exec",
            "environment.process.start",
            "environment.process.inspect",
            "environment.process.read_output",
            "environment.process.write_stdin",
            "environment.process.close_stdin",
            "environment.process.signal",
            "environment.process.wait",
            "environment.process.kill",
            "environment.process.release",
            "environment.output.read",
            "environment.output.release",
            "environment.port.inspect",
            "environment.port.wait",
        }
    )
)
_FAMILIES = frozenset({"files", "shell", "processes", "ports", "outputs", "state"})


def upgrade() -> None:
    """Apply the schema change."""
    with op.batch_alter_table("session_environment_resource", schema=None) as batch_op:
        batch_op.add_column(sa.Column("access", sa.String(length=16), nullable=True))

    connection = op.get_bind()
    resources = sa.table(
        "session_environment_resource",
        sa.column("session_id", sa.String()),
        sa.column("mount_name", sa.String()),
        sa.column("permission_ceiling_json", sa.Text()),
        sa.column("access", sa.String()),
    )
    rows = connection.execute(
        sa.select(resources.c.session_id, resources.c.mount_name, resources.c.permission_ceiling_json)
    )
    for row in rows:
        access = _legacy_access(row.permission_ceiling_json)
        connection.execute(
            resources.update()
            .where(resources.c.session_id == row.session_id)
            .where(resources.c.mount_name == row.mount_name)
            .values(access=access)
        )

    with op.batch_alter_table("session_environment_resource", schema=None) as batch_op:
        batch_op.alter_column("access", existing_type=sa.String(length=16), nullable=False)
        batch_op.drop_column("permission_ceiling_json")


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("session_environment_resource", schema=None) as batch_op:
        batch_op.add_column(sa.Column("permission_ceiling_json", sa.Text(), nullable=True))

    connection = op.get_bind()
    resources = sa.table(
        "session_environment_resource",
        sa.column("session_id", sa.String()),
        sa.column("mount_name", sa.String()),
        sa.column("permission_ceiling_json", sa.Text()),
        sa.column("access", sa.String()),
    )
    rows = connection.execute(sa.select(resources.c.session_id, resources.c.mount_name, resources.c.access))
    for row in rows:
        actions = {
            "read_only": _FILE_READ_ACTIONS | _STATE_ACTIONS,
            "read_write": _FILE_READ_ACTIONS | _FILE_MUTATION_ACTIONS | _STATE_ACTIONS,
            "full": _ALL_ACTIONS,
        }.get(row.access)
        if actions is None:
            raise RuntimeError("Stored Environment access cannot be downgraded.")
        connection.execute(
            resources.update()
            .where(resources.c.session_id == row.session_id)
            .where(resources.c.mount_name == row.mount_name)
            .values(permission_ceiling_json=json.dumps(sorted(actions), separators=(",", ":")))
        )

    with op.batch_alter_table("session_environment_resource", schema=None) as batch_op:
        batch_op.alter_column("permission_ceiling_json", existing_type=sa.Text(), nullable=False)
        batch_op.drop_column("access")


def _legacy_access(payload: str) -> str:
    try:
        values = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Stored Environment permission ceiling is invalid.") from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise RuntimeError("Stored Environment permission ceiling is invalid.")

    actions: set[str] = set()
    for value in values:
        if value == "files":
            actions.update(_FILE_READ_ACTIONS | _FILE_MUTATION_ACTIONS)
        elif value == "state":
            actions.update(_STATE_ACTIONS)
        elif value in {"shell", "processes", "ports", "outputs"}:
            actions.add(value)
        elif value in _ALL_ACTIONS:
            actions.add(value)
        elif value in _FAMILIES or value.startswith("environment."):
            raise RuntimeError("Stored Environment permission ceiling cannot be mapped safely.")
        else:
            raise RuntimeError("Stored Environment permission ceiling is invalid.")

    model_actions = actions - _STATE_ACTIONS
    if not model_actions:
        raise RuntimeError("Stored Environment permission ceiling cannot be mapped safely.")
    if model_actions <= _FILE_READ_ACTIONS:
        return "read_only"
    if model_actions <= _FILE_READ_ACTIONS | _FILE_MUTATION_ACTIONS:
        return "read_write"
    if any(value in {"shell", "processes", "ports", "outputs"} for value in model_actions) or any(
        value.startswith(_NON_FILE_ACTION_PREFIXES) for value in model_actions
    ):
        return "full"
    raise RuntimeError("Stored Environment permission ceiling cannot be mapped safely.")
