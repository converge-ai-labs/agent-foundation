"""enforce asset run attempt provenance.

Revision ID: 32fd540534f7
Revises: 8560ee8dfdab
Create Date: 2026-09-08 06:48:30.229723+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "32fd540534f7"
down_revision: str | Sequence[str] | None = "8560ee8dfdab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    # PostgreSQL validates existing provenance without rewriting Asset bodies;
    # uploads have a null Attempt reference and remain compatible with old workers.
    # SQLite rebuilds this metadata table through Alembic's batch implementation.
    with op.batch_alter_table("assets") as batch:
        batch.create_foreign_key(
            op.f("fk_assets_source_run_attempt_id_run_attempts"),
            "run_attempts",
            ["source_run_attempt_id", "organization_id"],
            ["id", "organization_id"],
        )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    with op.batch_alter_table("assets") as batch:
        batch.drop_constraint(op.f("fk_assets_source_run_attempt_id_run_attempts"), type_="foreignkey")
