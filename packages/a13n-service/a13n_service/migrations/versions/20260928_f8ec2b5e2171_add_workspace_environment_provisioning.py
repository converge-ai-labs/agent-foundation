"""add workspace environment provisioning

Revision ID: f8ec2b5e2171
Revises: b70fad768c94
"""

import sqlalchemy as sa
from alembic import op

revision = "f8ec2b5e2171"
down_revision = "b70fad768c94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_provisioning",
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("component", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(length=72), nullable=False),
        sa.Column("template_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("component IN ('local', 'docker')", name=op.f("ck_workspace_provisioning_component")),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name=op.f("fk_workspace_provisioning_organization_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("workspace_id", "component", name=op.f("pk_workspace_provisioning")),
    )
    op.alter_column("connector_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("connector_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("environment_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("environment_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("environment_templates", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("environment_templates", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("memory_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("memory_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("model_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("model_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("web_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.alter_column("web_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=True)
    op.execute(
        """
    CREATE TRIGGER refuse_mutation BEFORE UPDATE OR DELETE ON workspace_provisioning FOR EACH ROW EXECUTE FUNCTION refuse_mutation()
    """
    )


def downgrade() -> None:
    op.alter_column("web_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("web_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("model_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("model_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("memory_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("memory_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("environment_templates", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("environment_templates", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("environment_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("environment_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("connector_providers", "updated_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.alter_column("connector_providers", "created_by_id", existing_type=sa.VARCHAR(length=72), nullable=False)
    op.drop_table("workspace_provisioning")
