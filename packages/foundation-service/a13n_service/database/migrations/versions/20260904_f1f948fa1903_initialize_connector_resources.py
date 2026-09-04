"""initialize connector providers and connections.

Revision ID: f1f948fa1903
Revises: 4d09e6da2dd5
Create Date: 2026-09-04 08:22:08.891876+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1f948fa1903"
down_revision: str | Sequence[str] | None = "4d09e6da2dd5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "connectivity_commands",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=72), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=72), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=72), nullable=False),
        sa.Column("result_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service_account')", name=op.f("ck_connectivity_commands_actor_type_valid")
        ),
        sa.CheckConstraint("result_version >= 1", name=op.f("ck_connectivity_commands_result_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connectivity_commands")),
    )
    op.create_index(
        "ix_connectivity_commands_resource",
        "connectivity_commands",
        ["resource_type", "resource_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_connectivity_commands_request",
        "connectivity_commands",
        ["workspace_id", "actor_type", "actor_id", "operation", "scope_id", "idempotency_key_digest"],
        unique=True,
    )
    op.create_table(
        "connector_providers",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("configuration_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.Column("credential_generation", sa.BigInteger(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL",
            name=op.f("ck_connector_providers_credential_material_consistent"),
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_connector_providers_created_by_type_valid")
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_connector_providers_status_valid")),
        sa.CheckConstraint(
            "credential_generation >= 1", name=op.f("ck_connector_providers_credential_generation_positive")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_connector_providers_name_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_connector_providers_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_connector_providers_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_providers")),
    )
    op.create_index(
        "ix_connector_providers_driver_status", "connector_providers", ["type", "status", "id"], unique=False
    )
    op.create_index(
        "ix_connector_providers_workspace_updated",
        "connector_providers",
        ["workspace_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_connector_providers_id_tenant",
        "connector_providers",
        ["id", "organization_id", "workspace_id"],
        unique=True,
    )
    op.create_index(
        "uq_connector_providers_workspace_name", "connector_providers", ["workspace_id", "normalized_name"], unique=True
    )
    op.create_table(
        "connector_connections",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("connector_provider_id", sa.String(length=72), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=True),
        sa.Column("owner_id", sa.String(length=72), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("connector_key", sa.String(length=128), nullable=False),
        sa.Column("external_ref", sa.String(length=2048), nullable=True),
        sa.Column("safe_metadata_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_reason", sa.String(length=32), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("setup_generation", sa.BigInteger(), nullable=False),
        sa.Column("revoke_generation", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'action_required') = (status_reason IS NOT NULL)",
            name=op.f("ck_connector_connections_status_reason_valid"),
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')",
            name=op.f("ck_connector_connections_created_by_type_valid"),
        ),
        sa.CheckConstraint(
            "owner_type IS NULL OR owner_type IN ('user', 'service_account')",
            name=op.f("ck_connector_connections_owner_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'disabled') OR external_ref IS NOT NULL",
            name=op.f("ck_connector_connections_external_ref_required_when_bound"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'action_required', 'disabled')",
            name=op.f("ck_connector_connections_status_valid"),
        ),
        sa.CheckConstraint(
            "(owner_type IS NULL) = (owner_id IS NULL)", name=op.f("ck_connector_connections_owner_complete")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_connector_connections_name_bounded")),
        sa.CheckConstraint(
            "revoke_generation >= 0", name=op.f("ck_connector_connections_revoke_generation_non_negative")
        ),
        sa.CheckConstraint("setup_generation >= 1", name=op.f("ck_connector_connections_setup_generation_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_connector_connections_version_positive")),
        sa.ForeignKeyConstraint(
            ["connector_provider_id", "organization_id", "workspace_id"],
            ["connector_providers.id", "connector_providers.organization_id", "connector_providers.workspace_id"],
            name=op.f("fk_connector_connections_connector_provider_id_connector_providers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_connections")),
    )
    op.create_index(
        "ix_connector_connections_connector_status",
        "connector_connections",
        ["connector_provider_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_connector_connections_owner",
        "connector_connections",
        ["workspace_id", "owner_type", "owner_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_connector_connections_workspace_updated",
        "connector_connections",
        ["workspace_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_connector_connections_external_ref",
        "connector_connections",
        ["connector_provider_id", "external_ref"],
        unique=True,
    )
    op.create_index(
        "uq_connector_connections_id_tenant",
        "connector_connections",
        ["id", "organization_id", "workspace_id"],
        unique=True,
    )
    op.create_index(
        "uq_connector_connections_workspace_name",
        "connector_connections",
        ["workspace_id", "normalized_name"],
        unique=True,
    )
    op.create_table(
        "connector_connection_operations",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("connector_connection_id", sa.String(length=72), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.BigInteger(), nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("claim_owner", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('revoke')", name=op.f("ck_connector_connection_operations_kind_valid")),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'unknown', 'failed')",
            name=op.f("ck_connector_connection_operations_status_valid"),
        ),
        sa.CheckConstraint(
            "claim_generation >= 0", name=op.f("ck_connector_connection_operations_claim_generation_valid")
        ),
        sa.CheckConstraint("generation >= 1", name=op.f("ck_connector_connection_operations_generation_positive")),
        sa.ForeignKeyConstraint(
            ["connector_connection_id", "organization_id", "workspace_id"],
            ["connector_connections.id", "connector_connections.organization_id", "connector_connections.workspace_id"],
            name=op.f("fk_connector_connection_operations_connector_connection_id_connector_connections"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_connection_operations")),
    )
    op.create_index(
        "ix_connector_connection_operations_reconcile",
        "connector_connection_operations",
        ["status", "available_at", "claim_expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_connector_connection_operations_generation",
        "connector_connection_operations",
        ["connector_connection_id", "kind", "generation"],
        unique=True,
    )
    op.create_table(
        "connector_setup_attempts",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("connector_connection_id", sa.String(length=72), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("initiating_principal_type", sa.String(length=32), nullable=False),
        sa.Column("initiating_principal_id", sa.String(length=72), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=True),
        sa.Column("owner_id", sa.String(length=72), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("connector_key", sa.String(length=128), nullable=False),
        sa.Column("external_user_correlation", sa.String(length=128), nullable=False),
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("return_path", sa.String(length=2048), nullable=False),
        sa.Column("setup_json", sa.JSON(), nullable=False),
        sa.Column("external_ref", sa.String(length=2048), nullable=True),
        sa.Column("external_handle_digest", sa.String(length=64), nullable=True),
        sa.Column("supports_verified_callback", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.BigInteger(), nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("claim_owner", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "initiating_principal_type = 'user'", name=op.f("ck_connector_setup_attempts_initiating_user_required")
        ),
        sa.CheckConstraint(
            "owner_type IS NULL OR owner_type IN ('user', 'service_account')",
            name=op.f("ck_connector_setup_attempts_owner_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'attached', 'reserved', 'completed', 'failed', 'expired')",
            name=op.f("ck_connector_setup_attempts_status_valid"),
        ),
        sa.CheckConstraint(
            "(owner_type IS NULL) = (owner_id IS NULL)", name=op.f("ck_connector_setup_attempts_owner_complete")
        ),
        sa.CheckConstraint(
            "claim_generation >= 0", name=op.f("ck_connector_setup_attempts_claim_generation_non_negative")
        ),
        sa.CheckConstraint("generation >= 1", name=op.f("ck_connector_setup_attempts_generation_positive")),
        sa.ForeignKeyConstraint(
            ["connector_connection_id", "organization_id", "workspace_id"],
            ["connector_connections.id", "connector_connections.organization_id", "connector_connections.workspace_id"],
            name=op.f("fk_connector_setup_attempts_connector_connection_id_connector_connections"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_setup_attempts")),
    )
    op.create_index(
        "ix_connector_setup_attempts_expiry", "connector_setup_attempts", ["expires_at", "status", "id"], unique=False
    )
    op.create_index(
        "ix_connector_setup_attempts_reconcile",
        "connector_setup_attempts",
        ["status", "available_at", "claim_expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_connector_setup_attempts_connection_generation",
        "connector_setup_attempts",
        ["connector_connection_id", "generation"],
        unique=True,
    )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index("uq_connector_setup_attempts_connection_generation", table_name="connector_setup_attempts")
    op.drop_index("ix_connector_setup_attempts_reconcile", table_name="connector_setup_attempts")
    op.drop_index("ix_connector_setup_attempts_expiry", table_name="connector_setup_attempts")
    op.drop_table("connector_setup_attempts")
    op.drop_index("uq_connector_connection_operations_generation", table_name="connector_connection_operations")
    op.drop_index("ix_connector_connection_operations_reconcile", table_name="connector_connection_operations")
    op.drop_table("connector_connection_operations")
    op.drop_index("uq_connector_connections_workspace_name", table_name="connector_connections")
    op.drop_index("uq_connector_connections_id_tenant", table_name="connector_connections")
    op.drop_index("uq_connector_connections_external_ref", table_name="connector_connections")
    op.drop_index("ix_connector_connections_workspace_updated", table_name="connector_connections")
    op.drop_index("ix_connector_connections_owner", table_name="connector_connections")
    op.drop_index("ix_connector_connections_connector_status", table_name="connector_connections")
    op.drop_table("connector_connections")
    op.drop_index("uq_connector_providers_workspace_name", table_name="connector_providers")
    op.drop_index("uq_connector_providers_id_tenant", table_name="connector_providers")
    op.drop_index("ix_connector_providers_workspace_updated", table_name="connector_providers")
    op.drop_index("ix_connector_providers_driver_status", table_name="connector_providers")
    op.drop_table("connector_providers")
    op.drop_index("uq_connectivity_commands_request", table_name="connectivity_commands")
    op.drop_index("ix_connectivity_commands_resource", table_name="connectivity_commands")
    op.drop_table("connectivity_commands")
