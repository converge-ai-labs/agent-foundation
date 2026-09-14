"""initialize connections and authorizations.

Revision ID: 6a290c689265
Revises: f1f948fa1903
Create Date: 2026-09-04 08:22:17.588102+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a290c689265"
down_revision: str | Sequence[str] | None = "f1f948fa1903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=384), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_reason", sa.String(length=32), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("authorization_generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("setup_generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("credential_generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_check_json", sa.JSON(), nullable=True),
        sa.Column("safe_metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("connector_provider_id", sa.String(length=72), nullable=True),
        sa.Column("connector_key", sa.String(length=128), nullable=True),
        sa.Column("external_user_correlation", sa.String(length=128), nullable=True),
        sa.Column("external_ref", sa.String(length=2048), nullable=True),
        sa.Column("endpoint_url", sa.String(length=2048), nullable=True),
        sa.Column("auth_mode", sa.String(length=32), nullable=True),
        sa.Column("static_header_names_json", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("refresh_claim_generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("refresh_claim_owner", sa.String(length=128), nullable=True),
        sa.Column("refresh_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "(kind = 'connector' AND connector_provider_id IS NOT NULL AND connector_key IS NOT NULL AND endpoint_url IS NULL AND auth_mode IS NULL) OR (kind = 'mcp' AND endpoint_url IS NOT NULL AND auth_mode IN ('none', 'bearer', 'oauth', 'static_headers') AND connector_provider_id IS NULL AND connector_key IS NULL)",
            name=op.f("ck_connections_source_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'action_required') = (status_reason IS NOT NULL)",
            name=op.f("ck_connections_status_reason_valid"),
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_connections_created_by_type_valid")
        ),
        sa.CheckConstraint(
            "kind != 'connector' OR status != 'ready' OR (external_ref IS NOT NULL AND external_user_correlation IS NOT NULL)",
            name=op.f("ck_connections_verified_connector_binding_required"),
        ),
        sa.CheckConstraint("kind IN ('connector', 'mcp')", name=op.f("ck_connections_kind_valid")),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'action_required', 'disabled')", name=op.f("ck_connections_status_valid")
        ),
        sa.CheckConstraint(
            "status_reason IS NULL OR status_reason IN ('reauthorization_required', 'incompatible')",
            name=op.f("ck_connections_status_reason_value_valid"),
        ),
        sa.CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR (ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name=op.f("ck_connections_credential_material_consistent"),
        ),
        sa.CheckConstraint(
            "credential_generation >= 0 AND refresh_claim_generation >= 0",
            name=op.f("ck_connections_credential_generations_valid"),
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_connections_name_bounded")),
        sa.CheckConstraint("setup_generation >= 1", name=op.f("ck_connections_setup_generation_positive")),
        sa.CheckConstraint(
            "version >= 1 AND authorization_generation >= 1", name=op.f("ck_connections_versions_positive")
        ),
        sa.ForeignKeyConstraint(
            ["connector_provider_id", "organization_id"],
            ["connector_providers.id", "connector_providers.organization_id"],
            name=op.f("fk_connections_connector_provider_id_connector_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_connections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connections")),
    )
    op.create_index(
        "ix_connections_provider_status", "connections", ["connector_provider_id", "status", "id"], unique=False
    )
    op.create_index(
        "ix_connections_workspace_updated", "connections", ["workspace_id", "updated_at", "id"], unique=False
    )
    op.create_index(
        "uq_connections_external_ref", "connections", ["connector_provider_id", "external_ref"], unique=True
    )
    op.create_index("uq_connections_identity", "connections", ["id", "organization_id", "workspace_id"], unique=True)
    op.create_index("uq_connections_workspace_name", "connections", ["workspace_id", "normalized_name"], unique=True)
    op.create_table(
        "connection_authorizations",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("connection_id", sa.String(length=72), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("connection_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("initiating_principal_type", sa.String(length=32), server_default="user", nullable=False),
        sa.Column("initiating_principal_id", sa.String(length=72), nullable=False),
        sa.Column("completion_method", sa.String(length=32), server_default="polling", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("credential_generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("return_url", sa.String(length=2048), nullable=True),
        sa.Column("client_state", sa.String(length=512), nullable=True),
        sa.Column("completion_challenge", sa.String(length=64), nullable=True),
        sa.Column("launch_token_digest", sa.String(length=64), nullable=True),
        sa.Column("browser_binding_digest", sa.String(length=64), nullable=True),
        sa.Column("receipt_digest", sa.String(length=64), nullable=True),
        sa.Column("state_digest", sa.String(length=64), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("claim_owner", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=True),
        sa.Column("connector_key", sa.String(length=128), nullable=True),
        sa.Column("external_user_correlation", sa.String(length=128), nullable=True),
        sa.Column("external_ref", sa.String(length=2048), nullable=True),
        sa.Column("setup_ref", sa.String(length=2048), nullable=True),
        sa.Column("setup_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "completion_method IN ('polling', 'oauth_verifier', 'browser_confirmation', 'mcp_oauth', 'credentials')",
            name=op.f("ck_connection_authorizations_completion_method_valid"),
        ),
        sa.CheckConstraint(
            "initiating_principal_type IN ('user', 'service_account')",
            name=op.f("ck_connection_authorizations_initiator_valid"),
        ),
        sa.CheckConstraint("kind IN ('connector', 'mcp')", name=op.f("ck_connection_authorizations_kind_valid")),
        sa.CheckConstraint(
            "status IN ('pending', 'starting', 'attached', 'received', 'reserved', 'exchanging', 'completed', 'failed', 'expired', 'cancelled')",
            name=op.f("ck_connection_authorizations_status_valid"),
        ),
        sa.CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR (ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name=op.f("ck_connection_authorizations_credential_material_consistent"),
        ),
        sa.CheckConstraint(
            "credential_generation >= 0 AND claim_generation >= 0",
            name=op.f("ck_connection_authorizations_claim_generations_valid"),
        ),
        sa.CheckConstraint(
            "generation >= 1 AND connection_version >= 1", name=op.f("ck_connection_authorizations_versions_positive")
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "organization_id", "workspace_id"],
            ["connections.id", "connections.organization_id", "connections.workspace_id"],
            name=op.f("fk_connection_authorizations_connection_id_connections"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connection_authorizations")),
    )
    op.create_index(
        "ix_connection_authorizations_expiry", "connection_authorizations", ["expires_at", "status", "id"], unique=False
    )
    op.create_index(
        "ix_connection_authorizations_reconcile",
        "connection_authorizations",
        ["kind", "status", "available_at", "claim_expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_connection_authorizations_generation",
        "connection_authorizations",
        ["connection_id", "generation"],
        unique=True,
    )
    op.create_index("uq_connection_authorizations_state", "connection_authorizations", ["state_digest"], unique=True)
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("configuration_json", sa.JSON(), nullable=False),
        sa.Column("credential_generation", sa.BigInteger(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR (ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name=op.f("ck_mcp_oauth_clients_credential_material_consistent"),
        ),
        sa.CheckConstraint(
            "credential_generation >= 0", name=op.f("ck_mcp_oauth_clients_credential_generation_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["id", "organization_id", "workspace_id"],
            ["connections.id", "connections.organization_id", "connections.workspace_id"],
            name=op.f("fk_mcp_oauth_clients_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_oauth_clients")),
    )


def downgrade() -> None:
    op.drop_table("mcp_oauth_clients")
    op.drop_index("uq_connection_authorizations_state", table_name="connection_authorizations")
    op.drop_index("uq_connection_authorizations_generation", table_name="connection_authorizations")
    op.drop_index("ix_connection_authorizations_reconcile", table_name="connection_authorizations")
    op.drop_index("ix_connection_authorizations_expiry", table_name="connection_authorizations")
    op.drop_table("connection_authorizations")
    op.drop_index("uq_connections_workspace_name", table_name="connections")
    op.drop_index("uq_connections_identity", table_name="connections")
    op.drop_index("uq_connections_external_ref", table_name="connections")
    op.drop_index("ix_connections_workspace_updated", table_name="connections")
    op.drop_index("ix_connections_provider_status", table_name="connections")
    op.drop_table("connections")
