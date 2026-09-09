"""initialize identity secrets and durable operations.

Revision ID: 01929f3846a5
Revises:
Create Date: 2026-09-04 07:33:42.309482+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "01929f3846a5"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_index("uq_organizations_key", "organizations", ["key"], unique=True)
    op.create_table(
        "outbox_records",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=72), nullable=False),
        sa.Column("destination_kind", sa.String(length=64), nullable=False),
        sa.Column("destination_ref", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "(status = 'dead_lettered' AND dead_lettered_at IS NOT NULL) OR (status <> 'dead_lettered' AND dead_lettered_at IS NULL)",
            name=op.f("ck_outbox_records_dead_letter_shape_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'published' AND published_at IS NOT NULL) OR (status <> 'published' AND published_at IS NULL)",
            name=op.f("ck_outbox_records_published_shape_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'publishing' AND lease_expires_at IS NOT NULL) OR (status <> 'publishing' AND lease_expires_at IS NULL)",
            name=op.f("ck_outbox_records_lease_shape_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'dead_lettered')",
            name=op.f("ck_outbox_records_status_valid"),
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_outbox_records_attempt_count_non_negative")),
        sa.CheckConstraint("claim_generation >= 0", name=op.f("ck_outbox_records_claim_generation_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_records")),
        sa.UniqueConstraint(
            "source_kind", "source_id", "destination_kind", "destination_ref", name="uq_outbox_records_destination"
        ),
    )
    op.create_index(
        "ix_outbox_records_destination",
        "outbox_records",
        ["destination_kind", "destination_ref", "status", "id"],
        unique=False,
    )
    op.create_index("ix_outbox_records_due", "outbox_records", ["status", "available_at", "id"], unique=False)
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=True),
        sa.Column("workspace_id", sa.String(length=72), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=72), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=72), nullable=True),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column("credential_id", sa.String(length=72), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('anonymous', 'user', 'service_account', 'system')",
            name=op.f("ck_security_audit_events_actor_type_valid"),
        ),
        sa.CheckConstraint("outcome IN ('success', 'failure')", name=op.f("ck_security_audit_events_outcome_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_audit_events")),
    )
    op.create_index(
        "ix_security_audit_workspace_time",
        "security_audit_events",
        ["organization_id", "workspace_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_users_status_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("normalized_email", name=op.f("uq_users_normalized_email")),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workspaces_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_index(
        "uq_workspaces_organization_key",
        "workspaces",
        ["organization_id", "key"],
        unique=True,
    )
    op.create_index("uq_workspaces_id_organization_id", "workspaces", ["id", "organization_id"], unique=True)
    op.create_table(
        "idempotency_evidence",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=True),
        sa.Column("boundary_scope_id", sa.String(length=72), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=72), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=72), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("result_kind", sa.String(length=32), nullable=False),
        sa.Column("result_ref", sa.String(length=72), nullable=False),
        sa.Column("receipt_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service_account')", name=op.f("ck_idempotency_evidence_actor_type_valid")
        ),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_idempotency_evidence_expiry_after_creation")),
        sa.CheckConstraint("length(key_digest) = 64", name=op.f("ck_idempotency_evidence_key_digest_sha256")),
        sa.CheckConstraint("length(request_digest) = 64", name=op.f("ck_idempotency_evidence_request_digest_sha256")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_idempotency_evidence_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_idempotency_evidence_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_evidence")),
        sa.CheckConstraint(
            "boundary_scope_id = coalesce(workspace_id, organization_id)",
            name=op.f("ck_idempotency_evidence_boundary_scope_valid"),
        ),
        sa.UniqueConstraint(
            "boundary_scope_id",
            "actor_type",
            "actor_id",
            "operation",
            "scope_id",
            "key_digest",
            name="uq_idempotency_evidence_replay_scope",
        ),
    )
    op.create_index("ix_idempotency_evidence_expiry", "idempotency_evidence", ["expires_at", "id"], unique=False)
    op.create_table(
        "role_bindings",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=True),
        sa.Column("principal_type", sa.String(length=32), nullable=False),
        sa.Column("principal_id", sa.String(length=72), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=72), nullable=False),
        sa.Column("role_key", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "principal_type IN ('user', 'service_account')", name=op.f("ck_role_bindings_principal_type_valid")
        ),
        sa.CheckConstraint(
            "resource_type IN ('organization', 'workspace', 'agent')", name=op.f("ck_role_bindings_resource_type_valid")
        ),
        sa.CheckConstraint(
            "role_key IN ('member', 'viewer', 'runner', 'builder', 'admin')",
            name=op.f("ck_role_bindings_role_key_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_role_bindings_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_role_bindings_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_role_bindings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_bindings")),
    )
    op.create_index(
        "ix_role_bindings_authorization",
        "role_bindings",
        ["organization_id", "workspace_id", "principal_type", "principal_id"],
        unique=False,
    )
    op.create_index(
        "uq_role_bindings_principal_resource",
        "role_bindings",
        ["principal_type", "principal_id", "resource_type", "resource_id"],
        unique=True,
    )
    op.create_table(
        "secrets",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=72), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "owner_type != 'workspace' OR owner_id = workspace_id", name=op.f("ck_secrets_workspace_owner_consistent")
        ),
        sa.CheckConstraint(
            "owner_type IN ('workspace', 'user')",
            name=op.f("ck_secrets_owner_type_valid"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL) OR (deleted_at IS NOT NULL AND ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL)",
            name=op.f("ck_secrets_active_material_consistent"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_secrets_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_secrets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_secrets")),
    )
    op.create_index(
        "ix_secrets_owner_listing",
        "secrets",
        ["organization_id", "workspace_id", "owner_type", "owner_id", "key", "id"],
        unique=False,
    )
    op.create_index(
        "uq_secrets_active_owner_key",
        "secrets",
        ["organization_id", "workspace_id", "owner_type", "owner_id", "key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=384), nullable=False),
        sa.Column("description", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_service_accounts_status_valid")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_service_accounts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_accounts")),
    )
    op.create_index(
        "uq_service_accounts_active_workspace_normalized_name",
        "service_accounts",
        ["workspace_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index(
        "uq_service_accounts_active_workspace_normalized_name",
        table_name="service_accounts",
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("service_accounts")
    op.drop_index(
        "uq_secrets_active_owner_key",
        table_name="secrets",
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_secrets_owner_listing", table_name="secrets")
    op.drop_table("secrets")
    op.drop_index("uq_role_bindings_principal_resource", table_name="role_bindings")
    op.drop_index("ix_role_bindings_authorization", table_name="role_bindings")
    op.drop_table("role_bindings")
    op.drop_index("ix_idempotency_evidence_expiry", table_name="idempotency_evidence")
    op.drop_table("idempotency_evidence")
    op.drop_index("uq_workspaces_id_organization_id", table_name="workspaces")
    op.drop_index("uq_workspaces_organization_key", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("users")
    op.drop_index("ix_security_audit_workspace_time", table_name="security_audit_events")
    op.drop_table("security_audit_events")
    op.drop_index("ix_outbox_records_due", table_name="outbox_records")
    op.drop_index("ix_outbox_records_destination", table_name="outbox_records")
    op.drop_table("outbox_records")
    op.drop_index("uq_organizations_key", table_name="organizations")
    op.drop_table("organizations")
