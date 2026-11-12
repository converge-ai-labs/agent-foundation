"""add explicit connector group names.

Revision ID: 60710c8b48e4
Revises: 5304b6a9760a
Create Date: 2026-11-12 10:36:30.797645+00:00
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "60710c8b48e4"
down_revision: str | Sequence[str] | None = "5304b6a9760a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def upgrade() -> None:
    """Apply the schema change."""
    op.add_column("connector_configuration", sa.Column("group_name", sa.String(length=32), nullable=True))
    connection = op.get_bind()
    reserved: set[str] = set()
    rows = connection.execute(
        sa.text("SELECT connector_id, name, kind, deleted FROM connector_configuration ORDER BY connector_id")
    ).mappings()
    for row in rows:
        base = _normalize(row["name"]) or _normalize(row["kind"]) or "connection"
        if not base[0].isalpha():
            base = "connection-" + base
        base = base[:32].rstrip("-")
        candidate, number = base, 2
        while candidate in reserved:
            suffix = f"-{number}"
            candidate = base[: 32 - len(suffix)].rstrip("-") + suffix
            number += 1
        if not row["deleted"]:
            reserved.add(candidate)
        connection.execute(
            sa.text("UPDATE connector_configuration SET group_name = :group_name WHERE connector_id = :connector_id"),
            {"group_name": candidate, "connector_id": row["connector_id"]},
        )
    with op.batch_alter_table("connector_configuration") as batch_op:
        batch_op.alter_column("group_name", existing_type=sa.String(length=32), nullable=False)
        batch_op.create_index(
            "uq_connector_configuration_group_name", ["group_name"], unique=True, sqlite_where=sa.text("deleted = 0")
        )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("connector_configuration", schema=None) as batch_op:
        batch_op.drop_index("uq_connector_configuration_group_name", sqlite_where=sa.text("deleted = 0"))
        batch_op.drop_column("group_name")
