"""add accepted run environment mounts.

Revision ID: 0c63937b0134
Revises: c0355d7f89ba
Create Date: 2026-09-17 09:11:35.161444+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0c63937b0134"
down_revision: str | Sequence[str] | None = "c0355d7f89ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "run_environment_mounts",
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("environment_id", sa.String(length=72), nullable=False),
        sa.Column("access", sa.String(length=16), nullable=False),
        sa.Column("use_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("principal_type", sa.String(length=32), nullable=False),
        sa.Column("principal_id", sa.String(length=72), nullable=False),
        sa.Column("applied_attempt_id", sa.String(length=72), nullable=True),
        sa.Column("applied_attempt_fence", sa.BigInteger(), nullable=True),
        sa.Column("application_status", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.JSON(none_as_null=True), nullable=True),
        sa.CheckConstraint(
            "(applied_attempt_id IS NULL AND applied_attempt_fence IS NULL AND observed_at IS NULL AND application_status = 'pending' AND error IS NULL) OR (applied_attempt_id IS NOT NULL AND applied_attempt_fence IS NOT NULL AND applied_attempt_fence > 0 AND observed_at IS NOT NULL)",
            name=op.f("ck_run_environment_mounts_observation_authority_valid"),
        ),
        sa.CheckConstraint(
            "access IN ('read_only', 'read_write', 'full')", name=op.f("ck_run_environment_mounts_access_valid")
        ),
        sa.CheckConstraint(
            "application_status IN ('pending', 'preparing', 'ready', 'failed')",
            name=op.f("ck_run_environment_mounts_application_status_valid"),
        ),
        sa.CheckConstraint(
            "error IS NULL OR application_status = 'failed'", name=op.f("ck_run_environment_mounts_error_status_valid")
        ),
        sa.CheckConstraint(
            "name <> 'workspace' AND name ~ '^[a-z][a-z0-9-]{0,62}$'", name=op.f("ck_run_environment_mounts_name_valid")
        ),
        sa.CheckConstraint(
            "principal_type IN ('user', 'service_account')", name=op.f("ck_run_environment_mounts_principal_type_valid")
        ),
        sa.ForeignKeyConstraint(
            ["environment_id", "workspace_id"],
            ["environments.id", "environments.workspace_id"],
            name=op.f("fk_run_environment_mounts_environment_id_environments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "applied_attempt_id"],
            ["run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"],
            name=op.f("fk_run_environment_mounts_organization_id_run_attempts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name=op.f("fk_run_environment_mounts_organization_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_run_environment_mounts_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", "name", name=op.f("pk_run_environment_mounts")),
    )
    op.create_index(
        "ix_run_environment_mounts_acceptance", "run_environment_mounts", ["run_id", "created_at"], unique=True
    )
    op.create_index(
        "ix_run_environment_mounts_environment", "run_environment_mounts", ["environment_id", "run_id"], unique=False
    )


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_index("ix_run_environment_mounts_environment", table_name="run_environment_mounts")
    op.drop_index("ix_run_environment_mounts_acceptance", table_name="run_environment_mounts")
    op.drop_table("run_environment_mounts")
