"""initialize ingress routing and admission.

Revision ID: 676ce43cf244
Revises: 6a290c689265
Create Date: 2026-09-04 08:22:26.999041+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "676ce43cf244"
down_revision: str | Sequence[str] | None = "6a290c689265"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "application_accounts",
        sa.Column("identity_digest", sa.String(length=64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("provider_config_version", sa.String(length=64), nullable=False),
        sa.Column("provider_config_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credential_generation", sa.BigInteger(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_application_accounts_created_by_type_valid")
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_application_accounts_status_valid")),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL) OR (deleted_at IS NOT NULL AND ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL)",
            name=op.f("ck_application_accounts_credential_material_consistent"),
        ),
        sa.CheckConstraint(
            "credential_generation >= 1", name=op.f("ck_application_accounts_credential_generation_positive")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_application_accounts_name_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_application_accounts_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_application_accounts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_accounts")),
    )
    op.create_index(
        "ix_application_accounts_provider_status",
        "application_accounts",
        ["provider_key", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_application_accounts_workspace_updated",
        "application_accounts",
        ["workspace_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_application_accounts_id_tenant",
        "application_accounts",
        ["id", "organization_id", "workspace_id"],
        unique=True,
    )
    op.create_index(
        "uq_application_accounts_identity",
        "application_accounts",
        ["workspace_id", "provider_key", "identity_digest"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_application_accounts_workspace_name",
        "application_accounts",
        ["workspace_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "ingresses",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=72), nullable=False),
        sa.Column("provider_config_json", sa.JSON(), nullable=False),
        sa.Column("execution_service_account_id", sa.String(length=72), nullable=False),
        sa.Column("default_agent_id", sa.String(length=72), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_ingresses_created_by_type_valid")
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_ingresses_status_valid")),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_ingresses_name_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ingresses_version_positive")),
        sa.ForeignKeyConstraint(
            ["account_id", "organization_id", "workspace_id"],
            ["application_accounts.id", "application_accounts.organization_id", "application_accounts.workspace_id"],
            name=op.f("fk_ingresses_account_id_application_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["default_agent_id", "organization_id", "workspace_id"],
            ["agents.id", "agents.organization_id", "agents.workspace_id"],
            name=op.f("fk_ingresses_default_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_service_account_id"],
            ["service_accounts.id"],
            name=op.f("fk_ingresses_execution_service_account_id_service_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_ingresses_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingresses")),
        sa.UniqueConstraint("account_id", name="uq_ingresses_account"),
    )
    op.create_index("ix_ingresses_workspace_updated", "ingresses", ["workspace_id", "updated_at", "id"], unique=False)
    op.create_index("uq_ingresses_id_tenant", "ingresses", ["id", "organization_id", "workspace_id"], unique=True)
    op.create_index("uq_ingresses_workspace_name", "ingresses", ["workspace_id", "normalized_name"], unique=True)
    op.create_table(
        "agent_thread_bindings",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("ingress_id", sa.String(length=72), nullable=False),
        sa.Column("external_ref_kind", sa.String(length=128), nullable=False),
        sa.Column("external_ref_id", sa.String(length=2048), nullable=False),
        sa.Column("agent_id", sa.String(length=72), nullable=False),
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agent_thread_bindings_version_positive")),
        sa.ForeignKeyConstraint(
            ["agent_id", "organization_id", "workspace_id"],
            ["agents.id", "agents.organization_id", "agents.workspace_id"],
            name=op.f("fk_agent_thread_bindings_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingress_id", "organization_id", "workspace_id"],
            ["ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"],
            name=op.f("fk_agent_thread_bindings_ingress_id_ingresses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["threads.tenant_id", "threads.id"],
            name=op.f("fk_agent_thread_bindings_organization_id_threads"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_thread_bindings")),
        sa.UniqueConstraint(
            "ingress_id", "external_ref_kind", "external_ref_id", name="uq_agent_thread_bindings_external_ref"
        ),
    )
    op.create_index(
        "ix_agent_thread_bindings_thread", "agent_thread_bindings", ["organization_id", "thread_id", "id"], unique=False
    )
    op.create_index(
        "uq_agent_thread_bindings_id_tenant",
        "agent_thread_bindings",
        ["id", "organization_id", "workspace_id"],
        unique=True,
    )
    op.create_table(
        "ingress_agents",
        sa.Column("ingress_id", sa.String(length=72), nullable=False),
        sa.Column("agent_id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id", "organization_id", "workspace_id"],
            ["agents.id", "agents.organization_id", "agents.workspace_id"],
            name=op.f("fk_ingress_agents_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingress_id", "organization_id", "workspace_id"],
            ["ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"],
            name=op.f("fk_ingress_agents_ingress_id_ingresses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ingress_id", "agent_id", name=op.f("pk_ingress_agents")),
    )
    op.create_index(
        "ix_ingress_agents_agent", "ingress_agents", ["workspace_id", "agent_id", "ingress_id"], unique=False
    )
    op.create_table(
        "ingress_batches",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("ingress_id", sa.String(length=72), nullable=False),
        sa.Column("compatibility_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("event_count", sa.BigInteger(), nullable=False),
        sa.Column("event_bytes", sa.BigInteger(), nullable=False),
        sa.Column("append_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.BigInteger(), nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("claim_owner", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_kind", sa.String(length=32), nullable=True),
        sa.Column("result_id", sa.String(length=72), nullable=True),
        sa.Column("rejection_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')", name=op.f("ck_ingress_batches_status_valid")
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_ingress_batches_attempt_count_non_negative")),
        sa.CheckConstraint("claim_generation >= 0", name=op.f("ck_ingress_batches_claim_generation_non_negative")),
        sa.CheckConstraint("event_bytes >= 0", name=op.f("ck_ingress_batches_event_bytes_non_negative")),
        sa.CheckConstraint("event_count >= 1", name=op.f("ck_ingress_batches_event_count_positive")),
        sa.ForeignKeyConstraint(
            ["ingress_id", "organization_id", "workspace_id"],
            ["ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"],
            name=op.f("fk_ingress_batches_ingress_id_ingresses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingress_batches")),
    )
    op.create_index(
        "ix_ingress_batches_claim",
        "ingress_batches",
        ["status", "available_at", "claim_expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_ingress_batches_compatible",
        "ingress_batches",
        ["ingress_id", "compatibility_digest", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index("ix_ingress_batches_retention", "ingress_batches", ["status", "terminal_at", "id"], unique=False)
    op.create_index(
        "uq_ingress_batches_id_tenant", "ingress_batches", ["id", "organization_id", "workspace_id"], unique=True
    )
    op.create_table(
        "ingress_routes",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("ingress_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("provider_config_version", sa.String(length=64), nullable=False),
        sa.Column("match_json", sa.JSON(), nullable=False),
        sa.Column("agent_id", sa.String(length=72), nullable=True),
        sa.Column("input_mapping_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("min_interval_ms", sa.BigInteger(), nullable=False),
        sa.Column("max_batch_events", sa.BigInteger(), nullable=False),
        sa.Column("capability_overlays_json", sa.JSON(), nullable=False),
        sa.Column("provider_policy_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_ingress_routes_created_by_type_valid")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_ingress_routes_name_bounded")),
        sa.CheckConstraint("max_batch_events >= 1", name=op.f("ck_ingress_routes_max_batch_events_positive")),
        sa.CheckConstraint("min_interval_ms >= 1", name=op.f("ck_ingress_routes_min_interval_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ingress_routes_version_positive")),
        sa.ForeignKeyConstraint(
            ["agent_id", "organization_id", "workspace_id"],
            ["agents.id", "agents.organization_id", "agents.workspace_id"],
            name=op.f("fk_ingress_routes_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingress_id", "organization_id", "workspace_id"],
            ["ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"],
            name=op.f("fk_ingress_routes_ingress_id_ingresses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingress_routes")),
        sa.UniqueConstraint("ingress_id", "normalized_name", name="uq_ingress_routes_ingress_name"),
    )
    op.create_index(
        "ix_ingress_routes_listing", "ingress_routes", ["ingress_id", "enabled", "updated_at", "id"], unique=False
    )
    op.create_index(
        "uq_ingress_routes_id_tenant", "ingress_routes", ["id", "organization_id", "workspace_id"], unique=True
    )
    op.create_table(
        "ingress_admissions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("ingress_id", sa.String(length=72), nullable=False),
        sa.Column("ingress_version", sa.BigInteger(), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("provider_config_version", sa.String(length=64), nullable=False),
        sa.Column("route_id", sa.String(length=72), nullable=True),
        sa.Column("route_version", sa.BigInteger(), nullable=True),
        sa.Column("event_identity_kind", sa.String(length=128), nullable=False),
        sa.Column("event_identity_digest", sa.String(length=64), nullable=False),
        sa.Column("protected_event_identity", sa.String(length=2048), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("normalization_version", sa.String(length=128), nullable=False),
        sa.Column("event_json", sa.JSON(), nullable=False),
        sa.Column("ordering_key", sa.String(length=2048), nullable=False),
        sa.Column("raw_ref_json", sa.JSON(), nullable=True),
        sa.Column("selected_agent_id", sa.String(length=72), nullable=True),
        sa.Column("external_ref_kind", sa.String(length=128), nullable=True),
        sa.Column("external_ref_id", sa.String(length=2048), nullable=True),
        sa.Column("binding_state", sa.String(length=16), nullable=False),
        sa.Column("binding_id", sa.String(length=72), nullable=True),
        sa.Column("mapping_json", sa.JSON(), nullable=True),
        sa.Column("mapping_digest", sa.String(length=64), nullable=True),
        sa.Column("min_interval_ms", sa.BigInteger(), nullable=False),
        sa.Column("max_batch_events", sa.BigInteger(), nullable=False),
        sa.Column("provider_context_json", sa.JSON(), nullable=False),
        sa.Column("provider_policy_json", sa.JSON(), nullable=False),
        sa.Column("native_actions_json", sa.JSON(), nullable=False),
        sa.Column("capability_overlay_json", sa.JSON(), nullable=True),
        sa.Column("compatibility_digest", sa.String(length=64), nullable=False),
        sa.Column("event_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_kind", sa.String(length=32), nullable=True),
        sa.Column("result_id", sa.String(length=72), nullable=True),
        sa.Column("rejection_reason", sa.String(length=128), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.BigInteger(), nullable=False),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("claim_owner", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedup_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "binding_state IN ('bound', 'unbound')", name=op.f("ck_ingress_admissions_binding_state_valid")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')", name=op.f("ck_ingress_admissions_status_valid")
        ),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_ingress_admissions_attempt_count_non_negative")),
        sa.CheckConstraint("claim_generation >= 0", name=op.f("ck_ingress_admissions_claim_generation_non_negative")),
        sa.CheckConstraint("event_size_bytes >= 0", name=op.f("ck_ingress_admissions_event_size_non_negative")),
        sa.CheckConstraint("max_batch_events >= 1", name=op.f("ck_ingress_admissions_max_batch_events_positive")),
        sa.CheckConstraint("min_interval_ms >= 1", name=op.f("ck_ingress_admissions_min_interval_positive")),
        sa.ForeignKeyConstraint(
            ["binding_id", "organization_id", "workspace_id"],
            ["agent_thread_bindings.id", "agent_thread_bindings.organization_id", "agent_thread_bindings.workspace_id"],
            name=op.f("fk_ingress_admissions_binding_id_agent_thread_bindings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingress_id", "organization_id", "workspace_id"],
            ["ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"],
            name=op.f("fk_ingress_admissions_ingress_id_ingresses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["route_id", "organization_id", "workspace_id"],
            ["ingress_routes.id", "ingress_routes.organization_id", "ingress_routes.workspace_id"],
            name=op.f("fk_ingress_admissions_route_id_ingress_routes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selected_agent_id", "organization_id", "workspace_id"],
            ["agents.id", "agents.organization_id", "agents.workspace_id"],
            name=op.f("fk_ingress_admissions_selected_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingress_admissions")),
        sa.UniqueConstraint(
            "ingress_id", "event_identity_kind", "event_identity_digest", name="uq_ingress_admissions_identity"
        ),
    )
    op.create_index(
        "ix_ingress_admissions_capacity",
        "ingress_admissions",
        ["workspace_id", "ingress_id", "status", "event_size_bytes"],
        unique=False,
    )
    op.create_index(
        "ix_ingress_admissions_pending", "ingress_admissions", ["status", "available_at", "id"], unique=False
    )
    op.create_index(
        "ix_ingress_admissions_retention", "ingress_admissions", ["status", "dedup_expires_at", "id"], unique=False
    )
    op.create_index(
        "uq_ingress_admissions_id_tenant", "ingress_admissions", ["id", "organization_id", "workspace_id"], unique=True
    )
    op.create_table(
        "ingress_batch_events",
        sa.Column("batch_id", sa.String(length=72), nullable=False),
        sa.Column("admission_id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("ordering_key", sa.String(length=2048), nullable=False),
        sa.ForeignKeyConstraint(
            ["admission_id", "organization_id", "workspace_id"],
            ["ingress_admissions.id", "ingress_admissions.organization_id", "ingress_admissions.workspace_id"],
            name=op.f("fk_ingress_batch_events_admission_id_ingress_admissions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "organization_id", "workspace_id"],
            ["ingress_batches.id", "ingress_batches.organization_id", "ingress_batches.workspace_id"],
            name=op.f("fk_ingress_batch_events_batch_id_ingress_batches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("batch_id", "admission_id", name=op.f("pk_ingress_batch_events")),
        sa.UniqueConstraint("admission_id", name="uq_ingress_batch_events_admission"),
    )
    op.create_index(
        "ix_ingress_batch_events_order",
        "ingress_batch_events",
        ["batch_id", "ordering_key", "admission_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index("ix_ingress_batch_events_order", table_name="ingress_batch_events")
    op.drop_table("ingress_batch_events")
    op.drop_index("uq_ingress_admissions_id_tenant", table_name="ingress_admissions")
    op.drop_index("ix_ingress_admissions_retention", table_name="ingress_admissions")
    op.drop_index("ix_ingress_admissions_pending", table_name="ingress_admissions")
    op.drop_index("ix_ingress_admissions_capacity", table_name="ingress_admissions")
    op.drop_table("ingress_admissions")
    op.drop_index("uq_ingress_routes_id_tenant", table_name="ingress_routes")
    op.drop_index("ix_ingress_routes_listing", table_name="ingress_routes")
    op.drop_table("ingress_routes")
    op.drop_index("uq_ingress_batches_id_tenant", table_name="ingress_batches")
    op.drop_index("ix_ingress_batches_retention", table_name="ingress_batches")
    op.drop_index("ix_ingress_batches_compatible", table_name="ingress_batches")
    op.drop_index("ix_ingress_batches_claim", table_name="ingress_batches")
    op.drop_table("ingress_batches")
    op.drop_index("ix_ingress_agents_agent", table_name="ingress_agents")
    op.drop_table("ingress_agents")
    op.drop_index("uq_agent_thread_bindings_id_tenant", table_name="agent_thread_bindings")
    op.drop_index("ix_agent_thread_bindings_thread", table_name="agent_thread_bindings")
    op.drop_table("agent_thread_bindings")
    op.drop_index("ix_ingresses_workspace_updated", table_name="ingresses")
    op.drop_index("uq_ingresses_id_tenant", table_name="ingresses")
    op.drop_index("uq_ingresses_workspace_name", table_name="ingresses")
    op.drop_table("ingresses")
    op.drop_index("ix_application_accounts_provider_status", table_name="application_accounts")
    op.drop_index("ix_application_accounts_workspace_updated", table_name="application_accounts")
    op.drop_index("uq_application_accounts_identity", table_name="application_accounts")
    op.drop_index("uq_application_accounts_id_tenant", table_name="application_accounts")
    op.drop_index("uq_application_accounts_workspace_name", table_name="application_accounts")
    op.drop_table("application_accounts")
