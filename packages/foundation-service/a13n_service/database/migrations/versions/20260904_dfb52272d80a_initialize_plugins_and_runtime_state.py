"""initialize plugins and runtime state.

Revision ID: dfb52272d80a
Revises: 951f75b187a9
Create Date: 2026-09-04 08:21:12.147109+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dfb52272d80a"
down_revision: str | Sequence[str] | None = "951f75b187a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "plugin_runtime_locks",
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('on_demand', 'runner')", name=op.f("ck_plugin_runtime_locks_mode_valid")),
        sa.CheckConstraint("schema_version = '1'", name=op.f("ck_plugin_runtime_locks_schema_version_v1")),
        sa.CheckConstraint("length(digest) = 64", name=op.f("ck_plugin_runtime_locks_digest_sha256")),
        sa.PrimaryKeyConstraint("digest", name=op.f("pk_plugin_runtime_locks")),
    )
    op.create_index("ix_plugin_runtime_locks_created", "plugin_runtime_locks", ["created_at", "digest"], unique=False)
    op.create_table(
        "plugin_runtime_state",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("active_lock_digest", sa.String(length=64), nullable=True),
        sa.Column("runtime_generation", sa.BigInteger(), nullable=False),
        sa.Column("command_operation_id", sa.String(length=72), nullable=True),
        sa.Column("command_claim_generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("command_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 'runtime'", name=op.f("ck_plugin_runtime_state_singleton")),
        sa.CheckConstraint("mode IN ('on_demand', 'runner')", name=op.f("ck_plugin_runtime_state_mode_valid")),
        sa.CheckConstraint(
            "(command_operation_id IS NULL AND command_lease_expires_at IS NULL) OR (command_operation_id IS NOT NULL AND command_lease_expires_at IS NOT NULL)",
            name=op.f("ck_plugin_runtime_state_command_lease_shape_valid"),
        ),
        sa.CheckConstraint(
            "command_claim_generation >= 0", name=op.f("ck_plugin_runtime_state_command_claim_generation_non_negative")
        ),
        sa.CheckConstraint("runtime_generation >= 1", name=op.f("ck_plugin_runtime_state_runtime_generation_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plugin_runtime_state")),
    )
    op.create_table(
        "plugin_versions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("plugin_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.String(length=256), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_ref", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("requires_dist", sa.JSON(), nullable=False),
        sa.Column("requires_python", sa.String(length=1024), nullable=True),
        sa.Column("wheel_tags", sa.JSON(), nullable=False),
        sa.Column("root_is_purelib", sa.Boolean(), nullable=False),
        sa.Column("entry_point_target", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_plugin_versions_created_by_type_valid")
        ),
        sa.CheckConstraint("status = 'ready'", name=op.f("ck_plugin_versions_status_ready")),
        sa.CheckConstraint("length(content_digest) = 64", name=op.f("ck_plugin_versions_content_digest_sha256")),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_plugin_versions_size_bytes_positive")),
        sa.ForeignKeyConstraint(
            ["plugin_id"],
            ["plugins.id"],
            name=op.f("fk_plugin_versions_plugin_id_plugins"),
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plugin_versions")),
        sa.UniqueConstraint("id", "plugin_id", name="uq_plugin_versions_id_plugin"),
        sa.UniqueConstraint("plugin_id", "version", name="uq_plugin_versions_plugin_version"),
    )
    op.create_index("ix_plugin_versions_digest", "plugin_versions", ["content_digest"], unique=False)
    op.create_index("ix_plugin_versions_listing", "plugin_versions", ["plugin_id", "created_at", "id"], unique=False)
    op.create_table(
        "plugins",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("plugin_key", sa.String(length=128), nullable=False),
        sa.Column("distribution_name", sa.String(length=256), nullable=False),
        sa.Column("top_level_package", sa.String(length=256), nullable=False),
        sa.Column("active_version_id", sa.String(length=72), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_plugins_created_by_type_valid")
        ),
        sa.CheckConstraint("source IN ('builtin', 'uploaded')", name=op.f("ck_plugins_source_valid")),
        sa.ForeignKeyConstraint(
            ["active_version_id", "id"],
            ["plugin_versions.id", "plugin_versions.plugin_id"],
            name="fk_plugins_active_version_plugin_versions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plugins")),
        sa.UniqueConstraint("distribution_name", name="uq_plugins_distribution_name"),
        sa.UniqueConstraint("plugin_key", name="uq_plugins_plugin_key"),
        sa.UniqueConstraint("top_level_package", name="uq_plugins_top_level_package"),
    )
    op.create_index("ix_plugins_listing", "plugins", ["updated_at", "id"], unique=False)
    op.create_table(
        "plugin_runtime_tasks",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=72), nullable=False),
        sa.Column("command", sa.String(length=16), nullable=False),
        sa.Column("plugin_id", sa.String(length=72), nullable=False),
        sa.Column("plugin_version_id", sa.String(length=72), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("expected_runtime_generation", sa.BigInteger(), nullable=True),
        sa.Column("candidate_lock_digest", sa.String(length=64), nullable=True),
        sa.Column("staging_token", sa.String(length=512), nullable=True),
        sa.Column("committed_runtime_generation", sa.BigInteger(), nullable=True),
        sa.Column("result_refs", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(command = 'activate' AND plugin_version_id IS NOT NULL) OR (command = 'deactivate' AND plugin_version_id IS NULL)",
            name=op.f("ck_plugin_runtime_tasks_target_shape_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND phase IN ('accepted', 'candidate_ready', 'staged', 'committed') AND completed_at IS NULL) OR (status = 'succeeded' AND phase = 'succeeded' AND completed_at IS NOT NULL) OR (status = 'failed' AND phase = 'failed' AND completed_at IS NOT NULL)",
            name=op.f("ck_plugin_runtime_tasks_terminal_shape_valid"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service_account')", name=op.f("ck_plugin_runtime_tasks_actor_type_valid")
        ),
        sa.CheckConstraint("command IN ('activate', 'deactivate')", name=op.f("ck_plugin_runtime_tasks_command_valid")),
        sa.CheckConstraint(
            "phase IN ('accepted', 'candidate_ready', 'staged', 'committed', 'succeeded', 'failed')",
            name=op.f("ck_plugin_runtime_tasks_phase_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name=op.f("ck_plugin_runtime_tasks_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["candidate_lock_digest"],
            ["plugin_runtime_locks.digest"],
            name=op.f("fk_plugin_runtime_tasks_candidate_lock_digest_plugin_runtime_locks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plugin_id"], ["plugins.id"], name=op.f("fk_plugin_runtime_tasks_plugin_id_plugins"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["plugin_version_id", "plugin_id"],
            ["plugin_versions.id", "plugin_versions.plugin_id"],
            name=op.f("fk_plugin_runtime_tasks_plugin_version_id_plugin_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_plugin_runtime_tasks_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plugin_runtime_tasks")),
    )
    op.create_index(
        "ix_plugin_runtime_tasks_actor",
        "plugin_runtime_tasks",
        ["actor_type", "actor_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_plugin_runtime_tasks_reconcile", "plugin_runtime_tasks", ["status", "created_at", "id"], unique=False
    )
    op.create_table(
        "plugin_runtime_resolutions",
        sa.Column("operation_id", sa.String(length=72), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("runtime_lock_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(request_digest) = 64", name=op.f("ck_plugin_runtime_resolutions_request_digest_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["plugin_runtime_tasks.id"],
            name=op.f("fk_plugin_runtime_resolutions_operation_id_plugin_runtime_tasks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_lock_digest"],
            ["plugin_runtime_locks.digest"],
            name=op.f("fk_plugin_runtime_resolutions_runtime_lock_digest_plugin_runtime_locks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("operation_id", name=op.f("pk_plugin_runtime_resolutions")),
    )
    op.create_index(
        "ix_plugin_runtime_resolutions_lock",
        "plugin_runtime_resolutions",
        ["runtime_lock_digest", "operation_id"],
        unique=False,
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_plugins_active_version_plugin_versions",
            "plugins",
            "plugin_versions",
            ["active_version_id", "id"],
            ["id", "plugin_id"],
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            "fk_plugin_versions_plugin_id_plugins",
            "plugin_versions",
            "plugins",
            ["plugin_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_plugin_versions_plugin_id_plugins", "plugin_versions", type_="foreignkey")
        op.drop_constraint("fk_plugins_active_version_plugin_versions", "plugins", type_="foreignkey")
    op.drop_index("ix_plugin_runtime_resolutions_lock", table_name="plugin_runtime_resolutions")
    op.drop_table("plugin_runtime_resolutions")
    op.drop_index("ix_plugin_runtime_tasks_reconcile", table_name="plugin_runtime_tasks")
    op.drop_index("ix_plugin_runtime_tasks_actor", table_name="plugin_runtime_tasks")
    op.drop_table("plugin_runtime_tasks")
    op.drop_index("ix_plugins_listing", table_name="plugins")
    op.drop_table("plugins")
    op.drop_index("ix_plugin_versions_listing", table_name="plugin_versions")
    op.drop_index("ix_plugin_versions_digest", table_name="plugin_versions")
    op.drop_table("plugin_versions")
    op.drop_table("plugin_runtime_state")
    op.drop_index("ix_plugin_runtime_locks_created", table_name="plugin_runtime_locks")
    op.drop_table("plugin_runtime_locks")
