"""replace local store with continuation heads.

Revision ID: 03981368ed2a
Revises: 9958168d49de
Create Date: 2026-09-01 17:34:37.339630+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "03981368ed2a"
down_revision: str | Sequence[str] | None = "9958168d49de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_AUTHORITY_TABLES = (
    "current_configuration",
    "configuration_generation",
    "resource_revision",
    "composition_snapshot",
    "generation_resource",
    "skill_package_reference",
    "local_session",
    "session_environment_resource",
)


def upgrade() -> None:
    """Replace the unreleased resource-generation schema with continuation heads."""
    connection = op.get_bind()
    populated = [
        table
        for table in _OLD_AUTHORITY_TABLES
        if connection.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table}" LIMIT 1)')).scalar_one()
    ]
    if populated:
        names = ", ".join(populated)
        raise RuntimeError(
            "The pre-release Agent UI store contains state that cannot be mapped to the accepted "
            f"continuation schema ({names}). Export or remove the old data root before upgrading."
        )

    op.drop_table("session_environment_resource")
    op.drop_table("local_session")
    op.drop_table("skill_package_reference")
    op.drop_table("generation_resource")
    op.drop_table("current_configuration")
    op.drop_table("composition_snapshot")
    op.drop_table("resource_revision")
    op.drop_table("configuration_generation")

    op.create_table(
        "accepted_configuration",
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("yaml_digest", sa.String(length=64), nullable=False),
        sa.Column("document_json", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restart_required", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("source_digest", name=op.f("pk_accepted_configuration")),
    )
    op.create_table(
        "composition_snapshot",
        sa.Column("logical_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_kind", sa.String(length=16), nullable=False),
        sa.Column("object_schema_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "snapshot_kind IN ('agent', 'environment')",
            name=op.f("ck_composition_snapshot_snapshot_kind"),
        ),
        sa.PrimaryKeyConstraint("logical_digest", name=op.f("pk_composition_snapshot")),
        sa.UniqueConstraint("snapshot_kind", "logical_digest", name="identity"),
    )
    op.create_table(
        "current_configuration",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint("singleton_id = 1", name=op.f("ck_current_configuration_singleton")),
        sa.ForeignKeyConstraint(
            ["source_digest"],
            ["accepted_configuration.source_digest"],
            name=op.f("fk_current_configuration_source_digest_accepted_configuration"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("singleton_id", name=op.f("pk_current_configuration")),
        sa.UniqueConstraint("source_digest", name=op.f("uq_current_configuration_source_digest")),
    )
    op.create_table(
        "configuration_snapshot",
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("logical_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "snapshot_kind IN ('agent', 'environment')",
            name=op.f("ck_configuration_snapshot_snapshot_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["logical_digest"],
            ["composition_snapshot.logical_digest"],
            name=op.f("fk_configuration_snapshot_logical_digest_composition_snapshot"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_digest"],
            ["accepted_configuration.source_digest"],
            name=op.f("fk_configuration_snapshot_source_digest_accepted_configuration"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "source_digest",
            "snapshot_kind",
            "name",
            name=op.f("pk_configuration_snapshot"),
        ),
        sa.UniqueConstraint("source_digest", "snapshot_kind", "name", name="selection"),
    )
    op.create_table(
        "local_session",
        sa.Column("session_id", sa.String(length=80), nullable=False),
        sa.Column("root_thread_id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("agent_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("environment_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("parent_fork_json", sa.Text(), nullable=True),
        sa.Column("continuation_schema_version", sa.String(length=64), nullable=False),
        sa.Column("continuation_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint("status IN ('active', 'deleting')", name=op.f("ck_local_session_status")),
        sa.ForeignKeyConstraint(
            ["agent_snapshot_digest"],
            ["composition_snapshot.logical_digest"],
            name=op.f("fk_local_session_agent_snapshot_digest_composition_snapshot"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["environment_snapshot_digest"],
            ["composition_snapshot.logical_digest"],
            name=op.f("fk_local_session_environment_snapshot_digest_composition_snapshot"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_local_session")),
        sa.UniqueConstraint("root_thread_id", name=op.f("uq_local_session_root_thread_id")),
    )
    with op.batch_alter_table("local_session") as batch_op:
        batch_op.create_index(batch_op.f("ix_local_session_archived_at"), ["archived_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_local_session_pinned"), ["pinned"], unique=False)
        batch_op.create_index(batch_op.f("ix_local_session_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_local_session_updated_at"), ["updated_at"], unique=False)

    op.create_table(
        "child_thread",
        sa.Column("child_thread_id", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=80), nullable=False),
        sa.Column("parent_thread_id", sa.String(length=80), nullable=False),
        sa.Column("subagent_name", sa.String(length=63), nullable=False),
        sa.Column("child_definition_id", sa.String(length=256), nullable=False),
        sa.Column("child_definition_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["local_session.session_id"],
            name=op.f("fk_child_thread_session_id_local_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("child_thread_id", name=op.f("pk_child_thread")),
    )
    with op.batch_alter_table("child_thread") as batch_op:
        batch_op.create_index(batch_op.f("ix_child_thread_parent_thread_id"), ["parent_thread_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_child_thread_session_id"), ["session_id"], unique=False)

    op.create_table(
        "child_execution",
        sa.Column("execution_id", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=80), nullable=False),
        sa.Column("child_thread_id", sa.String(length=80), nullable=False),
        sa.Column("child_run_id", sa.String(length=80), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("selected_checkpoint_schema_version", sa.String(length=64), nullable=True),
        sa.Column("selected_checkpoint_digest", sa.String(length=64), nullable=True),
        sa.Column("selected_checkpoint_terminal", sa.Boolean(), nullable=False),
        sa.Column("resumable", sa.Boolean(), nullable=False),
        sa.Column("resumed_from", sa.String(length=80), nullable=True),
        sa.Column("failure_json", sa.Text(), nullable=True),
        sa.Column("owner_process_generation", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("segment_index >= 0", name=op.f("ck_child_execution_segment_index")),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'lost')",
            name=op.f("ck_child_execution_status"),
        ),
        sa.CheckConstraint(
            "(segment_index = 0 AND resumed_from IS NULL) OR (segment_index > 0 AND resumed_from IS NOT NULL)",
            name=op.f("ck_child_execution_resume_link"),
        ),
        sa.CheckConstraint(
            "selected_checkpoint_digest IS NOT NULL OR "
            "(selected_checkpoint_schema_version IS NULL AND selected_checkpoint_terminal = 0 AND resumable = 0)",
            name=op.f("ck_child_execution_checkpoint_fields"),
        ),
        sa.CheckConstraint(
            "status != 'succeeded' OR (selected_checkpoint_digest IS NOT NULL AND selected_checkpoint_terminal = 1)",
            name=op.f("ck_child_execution_success_checkpoint"),
        ),
        sa.ForeignKeyConstraint(
            ["child_thread_id"],
            ["child_thread.child_thread_id"],
            name=op.f("fk_child_execution_child_thread_id_child_thread"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resumed_from"],
            ["child_execution.execution_id"],
            name=op.f("fk_child_execution_resumed_from_child_execution"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["local_session.session_id"],
            name=op.f("fk_child_execution_session_id_local_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("execution_id", name=op.f("pk_child_execution")),
        sa.UniqueConstraint("child_thread_id", "segment_index", name="segment"),
        sa.UniqueConstraint("resumed_from", name=op.f("uq_child_execution_resumed_from")),
    )
    with op.batch_alter_table("child_execution") as batch_op:
        batch_op.create_index(batch_op.f("ix_child_execution_child_thread_id"), ["child_thread_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_child_execution_owner_process_generation"),
            ["owner_process_generation"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_child_execution_session_id"), ["session_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_child_execution_status"), ["status"], unique=False)

    op.create_table(
        "environment_binding",
        sa.Column("session_id", sa.String(length=80), nullable=False),
        sa.Column("profile_digest", sa.String(length=64), nullable=False),
        sa.Column("binder_key", sa.String(length=160), nullable=False),
        sa.Column("normalized_folder", sa.Text(), nullable=False),
        sa.Column("state_schema_version", sa.String(length=64), nullable=True),
        sa.Column("state_digest", sa.String(length=64), nullable=True),
        sa.Column("cleanup_status", sa.String(length=16), nullable=False),
        sa.Column("cleanup_failure_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cleanup_status IN ('none', 'required', 'in_progress', 'failed')",
            name=op.f("ck_environment_binding_cleanup_status"),
        ),
        sa.CheckConstraint(
            "state_digest IS NOT NULL OR state_schema_version IS NULL",
            name=op.f("ck_environment_binding_state_reference"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["local_session.session_id"],
            name=op.f("fk_environment_binding_session_id_local_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "session_id",
            "profile_digest",
            "binder_key",
            "normalized_folder",
            name=op.f("pk_environment_binding"),
        ),
    )
    with op.batch_alter_table("environment_binding") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_environment_binding_cleanup_status"),
            ["cleanup_status"],
            unique=False,
        )


def downgrade() -> None:
    """Reject an unsafe downgrade to the incompatible pre-release schema."""
    raise RuntimeError("The accepted continuation schema cannot be safely downgraded.")
