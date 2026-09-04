"""initialize environments and run bindings.

Revision ID: 568f8270be7a
Revises: 69a28e8783ad
Create Date: 2026-09-04 08:21:49.703184+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "568f8270be7a"
down_revision: str | Sequence[str] | None = "69a28e8783ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "environment_targets",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("provider_key", sa.String(length=128), nullable=False),
        sa.Column("identity_schema_version", sa.String(length=64), nullable=False),
        sa.Column("target_key", sa.String(length=1024), nullable=False),
        sa.Column("target_identity_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("retention_behavior", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("active_run_count", sa.BigInteger(), nullable=False),
        sa.Column("idle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retire_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("keeper_claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("keeper_owner_worker_generation", sa.String(length=256), nullable=True),
        sa.Column("keeper_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("keeper_source_binding_id", sa.String(length=72), nullable=True),
        sa.Column("operation_generation", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.String(length=72), nullable=True),
        sa.Column("requested_alive_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_alive_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_keepalive_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "((status = 'active' AND active_run_count > 0) OR (status IN ('idle', 'retired') AND active_run_count = 0))",
            name=op.f("ck_environment_targets_status_matches_active_run_count"),
        ),
        sa.CheckConstraint(
            "retention_behavior IN ('none', 'while_execution_active')",
            name=op.f("ck_environment_targets_retention_behavior_valid"),
        ),
        sa.CheckConstraint("status IN ('active', 'idle', 'retired')", name=op.f("ck_environment_targets_status_valid")),
        sa.CheckConstraint(
            "((keeper_owner_worker_generation IS NULL AND keeper_lease_expires_at IS NULL AND keeper_source_binding_id IS NULL) OR (keeper_owner_worker_generation IS NOT NULL AND keeper_lease_expires_at IS NOT NULL AND keeper_source_binding_id IS NOT NULL))",
            name=op.f("ck_environment_targets_keeper_claim_fields_together"),
        ),
        sa.CheckConstraint(
            "((operation_id IS NULL AND requested_alive_until IS NULL) OR (operation_id IS NOT NULL AND requested_alive_until IS NOT NULL))",
            name=op.f("ck_environment_targets_operation_fields_together"),
        ),
        sa.CheckConstraint("active_run_count >= 0", name=op.f("ck_environment_targets_active_run_count_non_negative")),
        sa.CheckConstraint(
            "keeper_claim_generation >= 0", name=op.f("ck_environment_targets_keeper_claim_generation_non_negative")
        ),
        sa.CheckConstraint(
            "length(target_identity_digest_sha256) = 64",
            name=op.f("ck_environment_targets_target_identity_digest_sha256"),
        ),
        sa.CheckConstraint(
            "length(target_key) BETWEEN 1 AND 1024", name=op.f("ck_environment_targets_target_key_bounded")
        ),
        sa.CheckConstraint(
            "operation_generation >= 0", name=op.f("ck_environment_targets_operation_generation_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environment_targets")),
        sa.UniqueConstraint(
            "provider_key",
            "identity_schema_version",
            "target_identity_digest_sha256",
            name="uq_environment_targets_identity",
        ),
    )
    op.create_index(
        "ix_environment_targets_keepalive_due",
        "environment_targets",
        ["status", "retention_behavior", "next_keepalive_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_environment_targets_retire_due", "environment_targets", ["status", "retire_after", "id"], unique=False
    )
    op.create_table(
        "environment_provider_selections",
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("provider_key", sa.String(length=128), nullable=False),
        sa.Column("provider_package_revision_id", sa.String(length=72), nullable=True),
        sa.Column("provider_lock", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by_type", sa.String(length=32), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "updated_by_type IN ('user', 'service_account')",
            name=op.f("ck_environment_provider_selections_updated_by_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_environment_provider_selections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "provider_key", name=op.f("pk_environment_provider_selections")),
    )
    op.create_index(
        "ix_environment_provider_selections_enabled",
        "environment_provider_selections",
        ["workspace_id", "enabled", "provider_key"],
        unique=False,
    )
    op.create_table(
        "environments",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=4096), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("current_revision_id", sa.String(length=72), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_type", sa.String(length=32), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_environments_created_by_type_valid")
        ),
        sa.CheckConstraint(
            "updated_by_type IN ('user', 'service_account')", name=op.f("ck_environments_updated_by_type_valid")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_environments_name_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_environments_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_environments_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environments")),
    )
    op.create_index(
        "ix_environments_workspace_updated", "environments", ["workspace_id", "updated_at", "id"], unique=False
    )
    op.create_index("uq_environments_id_tenant", "environments", ["id", "organization_id", "workspace_id"], unique=True)
    op.create_index("uq_environments_workspace_name", "environments", ["workspace_id", "normalized_name"], unique=True)
    op.create_table(
        "environment_revisions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("environment_id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("connection", sa.JSON(), nullable=False),
        sa.Column("provider_package_revision_id", sa.String(length=72), nullable=True),
        sa.Column("provider_lock", sa.JSON(), nullable=False),
        sa.Column("credential_bindings", sa.JSON(), nullable=False),
        sa.Column("access", sa.String(length=16), nullable=False),
        sa.Column("environment_target_id", sa.String(length=72), nullable=False),
        sa.Column("target_key", sa.String(length=1024), nullable=False),
        sa.Column("logical_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "access IN ('read_only', 'read_write', 'full')", name=op.f("ck_environment_revisions_access_valid")
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')",
            name=op.f("ck_environment_revisions_created_by_type_valid"),
        ),
        sa.CheckConstraint(
            "length(logical_digest_sha256) = 64", name=op.f("ck_environment_revisions_logical_digest_sha256")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_environment_revisions_version_positive")),
        sa.ForeignKeyConstraint(
            ["environment_id", "organization_id", "workspace_id"],
            ["environments.id", "environments.organization_id", "environments.workspace_id"],
            name=op.f("fk_environment_revisions_environment_id_environments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["environment_target_id"],
            ["environment_targets.id"],
            name=op.f("fk_environment_revisions_environment_target_id_environment_targets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environment_revisions")),
        sa.UniqueConstraint("environment_id", "version", name="uq_environment_revisions_number"),
        sa.UniqueConstraint("id", "environment_id", name="uq_environment_revisions_id_environment"),
    )
    op.create_index(
        "ix_environment_revisions_environment_desc",
        "environment_revisions",
        ["environment_id", "version", "id"],
        unique=False,
    )
    op.create_index(
        "uq_environment_revisions_id_tenant",
        "environment_revisions",
        ["id", "organization_id", "workspace_id"],
        unique=True,
    )
    op.create_table(
        "run_environment_bindings",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("mount_name", sa.String(length=32), nullable=False),
        sa.Column("source_environment_revision_id", sa.String(length=72), nullable=True),
        sa.Column("environment_target_id", sa.String(length=72), nullable=False),
        sa.Column("provider_key", sa.String(length=128), nullable=False),
        sa.Column("target_key", sa.String(length=1024), nullable=False),
        sa.Column("environment_execution_config_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mount_name = 'workspace'", name=op.f("ck_run_environment_bindings_mount_name_workspace")),
        sa.CheckConstraint(
            "length(environment_execution_config_digest_sha256) = 64",
            name=op.f("ck_run_environment_bindings_execution_config_digest_sha256"),
        ),
        sa.CheckConstraint(
            "length(target_key) BETWEEN 1 AND 1024", name=op.f("ck_run_environment_bindings_target_key_bounded")
        ),
        sa.ForeignKeyConstraint(
            ["environment_target_id"],
            ["environment_targets.id"],
            name=op.f("fk_run_environment_bindings_environment_target_id_environment_targets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            name=op.f("fk_run_environment_bindings_organization_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_environment_revision_id", "organization_id", "workspace_id"],
            ["environment_revisions.id", "environment_revisions.organization_id", "environment_revisions.workspace_id"],
            name=op.f("fk_run_environment_bindings_source_environment_revision_id_environment_revisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_run_environment_bindings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_environment_bindings")),
        sa.UniqueConstraint("organization_id", "run_id", name="uq_run_environment_bindings_run"),
    )
    op.create_index(
        "ix_run_environment_bindings_target",
        "run_environment_bindings",
        ["environment_target_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index("ix_run_environment_bindings_target", table_name="run_environment_bindings")
    op.drop_table("run_environment_bindings")
    op.drop_index("uq_environment_revisions_id_tenant", table_name="environment_revisions")
    op.drop_index("ix_environment_revisions_environment_desc", table_name="environment_revisions")
    op.drop_table("environment_revisions")
    op.drop_index("uq_environments_workspace_name", table_name="environments")
    op.drop_index("uq_environments_id_tenant", table_name="environments")
    op.drop_index("ix_environments_workspace_updated", table_name="environments")
    op.drop_table("environments")
    op.drop_index("ix_environment_provider_selections_enabled", table_name="environment_provider_selections")
    op.drop_table("environment_provider_selections")
    op.drop_index("ix_environment_targets_retire_due", table_name="environment_targets")
    op.drop_index("ix_environment_targets_keepalive_due", table_name="environment_targets")
    op.drop_table("environment_targets")
