"""initialize model providers and models.

Revision ID: 6fd6194d64ec
Revises: 01929f3846a5
Create Date: 2026-09-04 08:19:14.067453+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6fd6194d64ec"
down_revision: str | Sequence[str] | None = "01929f3846a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=384), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("credential_configured", sa.Boolean(), nullable=False),
        sa.Column("header_names", sa.JSON(), nullable=False),
        sa.Column("credential_generation", sa.BigInteger(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_type", sa.String(length=32), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_model_providers_created_by_type_valid")
        ),
        sa.CheckConstraint(
            "updated_by_type IN ('user', 'service_account')", name=op.f("ck_model_providers_updated_by_type_valid")
        ),
        sa.CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR (ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name=op.f("ck_model_providers_credential_material_consistent"),
        ),
        sa.CheckConstraint(
            "credential_generation >= 0", name=op.f("ck_model_providers_credential_generation_nonnegative")
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_model_providers_name_bounded")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_model_providers_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_model_providers_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_providers")),
    )
    op.create_index(
        "uq_model_providers_organization_normalized_name",
        "model_providers",
        ["organization_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index(
        "ix_model_providers_workspace_updated", "model_providers", ["workspace_id", "updated_at", "id"], unique=False
    )
    op.create_index("uq_model_providers_identity_scope", "model_providers", ["id", "organization_id"], unique=True)
    op.create_index(
        "uq_model_providers_workspace_name", "model_providers", ["workspace_id", "normalized_name"], unique=True
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("normalized_key", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2048), nullable=True),
        sa.Column("upstream_model", sa.String(length=256), nullable=False),
        sa.Column("catalog_ref", sa.JSON(), nullable=True),
        sa.Column("model_api", sa.String(length=96), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("declarations", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_type", sa.String(length=32), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_models_created_by_type_valid")
        ),
        sa.CheckConstraint(
            "updated_by_type IN ('user', 'service_account')", name=op.f("ck_models_updated_by_type_valid")
        ),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 128", name=op.f("ck_models_key_bounded")),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name=op.f("ck_models_name_bounded")),
        sa.CheckConstraint("length(upstream_model) BETWEEN 1 AND 256", name=op.f("ck_models_upstream_model_bounded")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_models_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id", "organization_id"],
            ["model_providers.id", "model_providers.organization_id"],
            name=op.f("fk_models_provider_id_model_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_models_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_models")),
    )
    op.create_index(
        "uq_models_organization_normalized_key",
        "models",
        ["organization_id", "normalized_key"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index("ix_models_provider", "models", ["provider_id", "id"], unique=False)
    op.create_index("ix_models_workspace_updated", "models", ["workspace_id", "updated_at", "id"], unique=False)
    op.create_index("uq_models_identity_scope", "models", ["id", "organization_id"], unique=True)
    op.create_index("uq_models_workspace_key", "models", ["workspace_id", "normalized_key"], unique=True)


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index("uq_models_workspace_key", table_name="models")
    op.drop_index("uq_models_identity_scope", table_name="models")
    op.drop_index("ix_models_workspace_updated", table_name="models")
    op.drop_index("ix_models_provider", table_name="models")
    op.drop_table("models")
    op.drop_index("uq_model_providers_workspace_name", table_name="model_providers")
    op.drop_index("uq_model_providers_identity_scope", table_name="model_providers")
    op.drop_index("ix_model_providers_workspace_updated", table_name="model_providers")
    op.drop_table("model_providers")
