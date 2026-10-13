"""add optional session memory and organization

Revision ID: decbf2aab878
Revises: 957733469712
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "decbf2aab878"
down_revision = "957733469712"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autogeneration does not detect changes to existing CHECK expressions.
    op.drop_constraint(op.f("ck_agents_preset"), "agents", type_="check")
    op.create_check_constraint(
        op.f("ck_agents_preset"),
        "agents",
        "(source = 'builtin' AND preset_kind IS NOT NULL AND preset_kind IN ('composer', 'finding', 'memory_organizer')) OR (source = 'custom' AND preset_kind IS NULL)",
    )
    op.create_table(
        "session_memories",
        sa.Column("session_id", sa.String(length=72), nullable=False),
        sa.Column("authority", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("agent_id", sa.String(length=72), nullable=True),
        sa.Column("revision_id", sa.String(length=72), nullable=True),
        sa.Column("pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_run_id", sa.String(length=72), nullable=True),
        sa.Column("last_run_id", sa.String(length=72), nullable=True),
        sa.Column("source_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("processed_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("failure", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["active_run_id"], ["runs.id"], name=op.f("fk_session_memories_active_run_id_runs")),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_session_memories_agent_id_agents")),
        sa.ForeignKeyConstraint(["last_run_id"], ["runs.id"], name=op.f("fk_session_memories_last_run_id_runs")),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["agent_revisions.id"], name=op.f("fk_session_memories_revision_id_agent_revisions")
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name=op.f("fk_session_memories_session_id_sessions")),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_session_memories")),
    )
    op.create_index(
        "ix_session_memories_pending",
        "session_memories",
        ["due_at"],
        unique=False,
        postgresql_where=sa.text("pending OR active_run_id IS NOT NULL"),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "memory", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.add_column("sessions", sa.Column("memory_id", sa.String(length=72), nullable=True))
    op.create_foreign_key(
        op.f("fk_sessions_memory_id_memories"), "sessions", "memories", ["memory_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("threads", sa.Column("purpose", sa.String(), server_default="conversation", nullable=False))
    op.create_check_constraint(
        op.f("ck_threads_purpose"), "threads", "purpose IN ('conversation', 'memory_organization')"
    )
    op.create_check_constraint(
        op.f("ck_threads_purpose_origin"), "threads", "purpose = 'conversation' OR origin = 'new'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_agents_preset"), "agents", type_="check")
    op.create_check_constraint(
        op.f("ck_agents_preset"),
        "agents",
        "(source = 'builtin' AND preset_kind IS NOT NULL AND preset_kind IN ('composer', 'finding')) OR (source = 'custom' AND preset_kind IS NULL)",
    )
    op.drop_constraint(op.f("ck_threads_purpose_origin"), "threads", type_="check")
    op.drop_constraint(op.f("ck_threads_purpose"), "threads", type_="check")
    op.drop_column("threads", "purpose")
    op.drop_constraint(op.f("fk_sessions_memory_id_memories"), "sessions", type_="foreignkey")
    op.drop_column("sessions", "memory_id")
    op.drop_column("sessions", "memory")
    op.drop_index(
        "ix_session_memories_pending",
        table_name="session_memories",
        postgresql_where=sa.text("pending OR active_run_id IS NOT NULL"),
    )
    op.drop_table("session_memories")
