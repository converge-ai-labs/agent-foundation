"""add memory spaces and organization

Revision ID: 8319baf5169b
Revises: 628ef18fdc03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "8319baf5169b"
down_revision = "628ef18fdc03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autogeneration does not detect changed CHECK expressions.
    op.drop_constraint(op.f("ck_agents_preset"), "agents", type_="check")
    op.create_check_constraint(
        op.f("ck_agents_preset"),
        "agents",
        "(source = 'builtin' AND preset_kind IS NOT NULL AND preset_kind IN ('composer', 'finding', 'memory_organizer')) OR (source = 'custom' AND preset_kind IS NULL)",
    )
    op.create_table(
        "memory_spaces",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("memory_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("organization", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("authority", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["principals.id"], name=op.f("fk_memory_spaces_created_by_id_principals")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name=op.f("fk_memory_spaces_organization_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_memory_spaces_organization_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["principals.id"], name=op.f("fk_memory_spaces_updated_by_id_principals")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "memory_id"],
            ["memories.workspace_id", "memories.id"],
            name=op.f("fk_memory_spaces_workspace_id_memories"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_spaces")),
        sa.UniqueConstraint("memory_id", name=op.f("uq_memory_spaces_memory_id")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_memory_spaces_workspace_id_id")),
    )
    op.create_table(
        "memory_space_organizations",
        sa.Column("space_id", sa.String(length=72), nullable=False),
        sa.Column("session_id", sa.String(length=72), nullable=True),
        sa.Column("pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_run_id", sa.String(length=72), nullable=True),
        sa.Column("last_run_id", sa.String(length=72), nullable=True),
        sa.Column("source_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("observed_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("processed_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("configuration_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("history_gap", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failure", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["active_run_id"], ["runs.id"], name=op.f("fk_memory_space_organizations_active_run_id_runs")
        ),
        sa.ForeignKeyConstraint(
            ["last_run_id"], ["runs.id"], name=op.f("fk_memory_space_organizations_last_run_id_runs")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name=op.f("fk_memory_space_organizations_session_id_sessions")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["memory_spaces.id"],
            name=op.f("fk_memory_space_organizations_space_id_memory_spaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("space_id", name=op.f("pk_memory_space_organizations")),
        sa.UniqueConstraint("session_id", name=op.f("uq_memory_space_organizations_session_id")),
    )
    op.create_index("ix_memory_space_organizations_due", "memory_space_organizations", ["due_at"], unique=False)
    op.add_column(
        "sessions",
        sa.Column(
            "memory", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.add_column(
        "sessions", sa.Column("purpose", sa.String(), server_default=sa.text("'conversation'"), nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_sessions_purpose"), "sessions", "purpose IN ('conversation', 'memory_organization')"
    )
    op.add_column("threads", sa.Column("purpose", sa.String(), server_default="conversation", nullable=False))
    op.create_check_constraint(
        op.f("ck_threads_purpose"), "threads", "purpose IN ('conversation', 'memory_organization')"
    )
    op.create_check_constraint(
        op.f("ck_threads_purpose_origin"), "threads", "purpose = 'conversation' OR origin = 'new'"
    )
    op.execute(
        """
    CREATE TRIGGER stamp_resource BEFORE UPDATE ON memory_spaces FOR EACH ROW EXECUTE FUNCTION stamp_resource()
    """
    )
    op.execute(
        """
    CREATE TRIGGER guard_identity BEFORE UPDATE ON memory_spaces FOR EACH ROW EXECUTE FUNCTION guard_identity()
    """
    )


def downgrade() -> None:
    # A populated deployment rolls forward; downgrade is for empty test databases.
    op.drop_constraint(op.f("ck_agents_preset"), "agents", type_="check")
    op.create_check_constraint(
        op.f("ck_agents_preset"),
        "agents",
        "(source = 'builtin' AND preset_kind IS NOT NULL AND preset_kind IN ('composer', 'finding')) OR (source = 'custom' AND preset_kind IS NULL)",
    )
    op.drop_constraint(op.f("ck_threads_purpose_origin"), "threads", type_="check")
    op.drop_constraint(op.f("ck_threads_purpose"), "threads", type_="check")
    op.drop_column("threads", "purpose")
    op.drop_constraint(op.f("ck_sessions_purpose"), "sessions", type_="check")
    op.drop_column("sessions", "purpose")
    op.drop_column("sessions", "memory")
    op.drop_index("ix_memory_space_organizations_due", table_name="memory_space_organizations")
    op.drop_table("memory_space_organizations")
    op.drop_table("memory_spaces")
