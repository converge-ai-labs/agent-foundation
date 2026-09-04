"""initialize mcp connections.

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
    """Create the domain schema."""
    op.create_table(
        "mcp_connections",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("owner_user_id", sa.String(length=72), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("endpoint_url", sa.String(length=2048), nullable=False),
        sa.Column("auth_mode", sa.String(length=32), nullable=False),
        sa.Column("static_header_names_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_reason", sa.String(length=32), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("credential_secret_id", sa.String(length=72), nullable=True),
        sa.Column("credential_generation", sa.BigInteger(), nullable=False),
        sa.Column("refresh_claim_generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("refresh_claim_owner", sa.String(length=128), nullable=True),
        sa.Column("refresh_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "refresh_available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("refresh_last_error_code", sa.String(length=128), nullable=True),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False),
        sa.Column("cleanup_attempt_count", sa.BigInteger(), nullable=False),
        sa.Column("cleanup_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleanup_last_error_code", sa.String(length=128), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'action_required') = (status_reason IS NOT NULL)",
            name=op.f("ck_mcp_connections_status_reason_valid"),
        ),
        sa.CheckConstraint(
            "auth_mode IN ('none', 'bearer', 'oauth', 'static_headers')",
            name=op.f("ck_mcp_connections_auth_mode_valid"),
        ),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_mcp_connections_created_by_type_valid")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'action_required', 'disabled')",
            name=op.f("ck_mcp_connections_status_valid"),
        ),
        sa.CheckConstraint(
            "status_reason IS NULL OR status_reason IN ('reauthorization_required', 'incompatible')",
            name=op.f("ck_mcp_connections_status_reason_value_valid"),
        ),
        sa.CheckConstraint(
            "cleanup_attempt_count >= 0", name=op.f("ck_mcp_connections_cleanup_attempt_count_non_negative")
        ),
        sa.CheckConstraint(
            "credential_generation >= 0", name=op.f("ck_mcp_connections_credential_generation_non_negative")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_mcp_connections_name_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_mcp_connections_version_positive")),
        sa.CheckConstraint(
            "refresh_claim_generation >= 0", name=op.f("ck_mcp_connections_refresh_claim_generation_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["credential_secret_id"],
            ["secrets.id"],
            name=op.f("fk_mcp_connections_credential_secret_id_secrets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_mcp_connections_owner_user_id_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_mcp_connections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_connections")),
    )
    op.create_index(
        "ix_mcp_connections_refresh_reconcile",
        "mcp_connections",
        ["status", "refresh_available_at", "refresh_claim_expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_connections_cleanup", "mcp_connections", ["cleanup_pending", "cleanup_available_at", "id"], unique=False
    )
    op.create_index(
        "ix_mcp_connections_owner", "mcp_connections", ["workspace_id", "owner_user_id", "status", "id"], unique=False
    )
    op.create_index(
        "ix_mcp_connections_workspace_updated", "mcp_connections", ["workspace_id", "updated_at", "id"], unique=False
    )
    op.create_index(
        "uq_mcp_connections_id_tenant", "mcp_connections", ["id", "organization_id", "workspace_id"], unique=True
    )
    op.create_index(
        "uq_mcp_connections_workspace_name", "mcp_connections", ["workspace_id", "normalized_name"], unique=True
    )
    op.create_table(
        "mcp_oauth_sessions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("mcp_connection_id", sa.String(length=72), nullable=False),
        sa.Column("initiating_user_id", sa.String(length=72), nullable=False),
        sa.Column("state_digest", sa.String(length=64), nullable=False),
        sa.Column("resource_url", sa.String(length=2048), nullable=False),
        sa.Column("issuer_url", sa.String(length=2048), nullable=False),
        sa.Column("authorization_endpoint", sa.String(length=2048), nullable=False),
        sa.Column("token_endpoint", sa.String(length=2048), nullable=False),
        sa.Column("registration_endpoint", sa.String(length=2048), nullable=True),
        sa.Column("client_id", sa.String(length=2048), nullable=True),
        sa.Column("scope", sa.String(length=2048), nullable=True),
        sa.Column("setup_secret_id", sa.String(length=72), nullable=False),
        sa.Column("setup_secret_generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("claim_owner", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'exchanging', 'completed', 'failed', 'expired')",
            name=op.f("ck_mcp_oauth_sessions_status_valid"),
        ),
        sa.CheckConstraint("claim_generation >= 0", name=op.f("ck_mcp_oauth_sessions_claim_generation_non_negative")),
        sa.ForeignKeyConstraint(
            ["initiating_user_id"],
            ["users.id"],
            name=op.f("fk_mcp_oauth_sessions_initiating_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mcp_connection_id", "organization_id", "workspace_id"],
            ["mcp_connections.id", "mcp_connections.organization_id", "mcp_connections.workspace_id"],
            name=op.f("fk_mcp_oauth_sessions_mcp_connection_id_mcp_connections"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["setup_secret_id"],
            ["secrets.id"],
            name=op.f("fk_mcp_oauth_sessions_setup_secret_id_secrets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_oauth_sessions")),
    )
    op.create_index(
        "ix_mcp_oauth_sessions_connection",
        "mcp_oauth_sessions",
        ["mcp_connection_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index("ix_mcp_oauth_sessions_expiry", "mcp_oauth_sessions", ["status", "expires_at", "id"], unique=False)
    op.create_index("uq_mcp_oauth_sessions_state", "mcp_oauth_sessions", ["state_digest"], unique=True)


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index("uq_mcp_oauth_sessions_state", table_name="mcp_oauth_sessions")
    op.drop_index("ix_mcp_oauth_sessions_expiry", table_name="mcp_oauth_sessions")
    op.drop_index("ix_mcp_oauth_sessions_connection", table_name="mcp_oauth_sessions")
    op.drop_table("mcp_oauth_sessions")
    op.drop_index("uq_mcp_connections_workspace_name", table_name="mcp_connections")
    op.drop_index("uq_mcp_connections_id_tenant", table_name="mcp_connections")
    op.drop_index("ix_mcp_connections_workspace_updated", table_name="mcp_connections")
    op.drop_index("ix_mcp_connections_owner", table_name="mcp_connections")
    op.drop_index("ix_mcp_connections_cleanup", table_name="mcp_connections")
    op.drop_index("ix_mcp_connections_refresh_reconcile", table_name="mcp_connections")
    op.drop_table("mcp_connections")
