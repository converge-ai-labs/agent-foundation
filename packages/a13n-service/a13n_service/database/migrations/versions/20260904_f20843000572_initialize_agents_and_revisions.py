"""initialize agents and revisions.

Revision ID: f20843000572
Revises: 951f75b187a9
Create Date: 2026-09-04 08:21:21.266510+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f20843000572"
down_revision: str | Sequence[str] | None = "951f75b187a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "agents",
        sa.Column("default_environment_template_id", sa.String(length=72), nullable=True),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=384), nullable=False),
        sa.Column("description", sa.String(length=4096), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("current_revision_id", sa.String(length=72), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duplicated_from_agent_id", sa.String(length=72), nullable=True),
        sa.Column("duplicated_from_revision_id", sa.String(length=72), nullable=True),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_type", sa.String(length=32), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account', 'system')", name=op.f("ck_agents_created_by_type_valid")
        ),
        sa.CheckConstraint("source IN ('builtin', 'custom')", name=op.f("ck_agents_source_valid")),
        sa.CheckConstraint(
            "updated_by_type IN ('user', 'service_account', 'system')", name=op.f("ck_agents_updated_by_type_valid")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_agents_name_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agents_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_agents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
    )
    op.create_index(
        "ix_agents_workspace_availability",
        "agents",
        ["workspace_id", "enabled", "archived_at", "updated_at", "id"],
        unique=False,
    )
    op.create_index("ix_agents_workspace_updated", "agents", ["workspace_id", "updated_at", "id"], unique=False)
    op.create_index("uq_agents_id_organization", "agents", ["id", "organization_id", "workspace_id"], unique=True)
    op.create_index("uq_agents_workspace_name", "agents", ["workspace_id", "normalized_name"], unique=True)
    op.create_table(
        "agent_revisions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("agent_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("resolved_model", sa.JSON(), nullable=False),
        sa.Column("resolved_plugins", sa.JSON(), nullable=False),
        sa.Column("resolved_skills", sa.JSON(), nullable=False),
        sa.Column("connector_tools", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("mcp_tools", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("resolved_subagents", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("source_revision_id", sa.String(length=72), nullable=True),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account', 'system')",
            name=op.f("ck_agent_revisions_created_by_type_valid"),
        ),
        sa.CheckConstraint("length(config_digest) = 64", name=op.f("ck_agent_revisions_config_digest_sha256")),
        sa.CheckConstraint("length(content_digest) = 64", name=op.f("ck_agent_revisions_content_digest_sha256")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agent_revisions_version_positive")),
        sa.ForeignKeyConstraint(
            ["agent_id", "organization_id", "workspace_id"],
            ["agents.id", "agents.organization_id", "agents.workspace_id"],
            name=op.f("fk_agent_revisions_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_revisions")),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_revisions_agent_number"),
    )
    op.create_index("ix_agent_revisions_agent_desc", "agent_revisions", ["agent_id", "version", "id"], unique=False)
    op.create_index(
        "uq_agent_revisions_id_organization", "agent_revisions", ["id", "organization_id", "workspace_id"], unique=True
    )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index("uq_agent_revisions_id_organization", table_name="agent_revisions")
    op.drop_index("ix_agent_revisions_agent_desc", table_name="agent_revisions")
    op.drop_table("agent_revisions")
    op.drop_index("uq_agents_workspace_name", table_name="agents")
    op.drop_index("uq_agents_id_organization", table_name="agents")
    op.drop_index("ix_agents_workspace_updated", table_name="agents")
    op.drop_index("ix_agents_workspace_availability", table_name="agents")
    op.drop_table("agents")
