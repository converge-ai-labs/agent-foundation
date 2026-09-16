"""initialize assets and skills.

Revision ID: 951f75b187a9
Revises: 6fd6194d64ec
Create Date: 2026-09-04 08:21:03.109186+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "951f75b187a9"
down_revision: str | Sequence[str] | None = "6fd6194d64ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_principal_type", sa.String(length=32), nullable=True),
        sa.Column("source_principal_id", sa.String(length=72), nullable=True),
        sa.Column("source_run_attempt_id", sa.String(length=72), nullable=True),
        sa.Column("source_invocation_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(source_kind = 'upload' AND source_principal_type IS NOT NULL AND source_principal_id IS NOT NULL AND source_run_attempt_id IS NULL AND source_invocation_id IS NULL) OR (source_kind = 'run_output' AND source_principal_type IS NULL AND source_principal_id IS NULL AND source_run_attempt_id IS NOT NULL AND source_invocation_id IS NOT NULL)",
            name=op.f("ck_assets_source_shape_valid"),
        ),
        sa.CheckConstraint("source_kind IN ('upload', 'run_output')", name=op.f("ck_assets_source_kind_valid")),
        sa.CheckConstraint(
            "source_principal_type IS NULL OR source_principal_type IN ('user', 'service_account')",
            name=op.f("ck_assets_source_principal_type_valid"),
        ),
        sa.CheckConstraint("content_sha256 = lower(content_sha256)", name=op.f("ck_assets_content_sha256_lowercase")),
        sa.CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at", name=op.f("ck_assets_deletion_after_creation")
        ),
        sa.CheckConstraint("filename = trim(filename)", name=op.f("ck_assets_filename_trimmed")),
        sa.CheckConstraint("length(content_sha256) = 64", name=op.f("ck_assets_content_sha256_length")),
        sa.CheckConstraint("length(filename) BETWEEN 1 AND 256", name=op.f("ck_assets_filename_bounded")),
        sa.CheckConstraint("length(media_type) BETWEEN 3 AND 255", name=op.f("ck_assets_media_type_bounded")),
        sa.CheckConstraint("media_type = lower(media_type)", name=op.f("ck_assets_media_type_lowercase")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_assets_size_bytes_non_negative")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_assets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
    )
    op.create_index(
        "ix_assets_active_run_attempt_listing",
        "assets",
        [
            "organization_id",
            "workspace_id",
            "source_run_attempt_id",
            sa.literal_column("created_at DESC"),
            sa.literal_column("id DESC"),
        ],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_assets_active_source_kind_listing",
        "assets",
        [
            "organization_id",
            "workspace_id",
            "source_kind",
            sa.literal_column("created_at DESC"),
            sa.literal_column("id DESC"),
        ],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_assets_active_workspace_listing",
        "assets",
        ["organization_id", "workspace_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_assets_tombstone_retention",
        "assets",
        ["deleted_at", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_index(
        "uq_assets_run_invocation",
        "assets",
        ["source_run_attempt_id", "source_invocation_id"],
        unique=True,
        postgresql_where=sa.text("source_kind = 'run_output'"),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("current_revision_id", sa.String(length=72), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_type", sa.String(length=32), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_skills_created_by_type_valid")
        ),
        sa.CheckConstraint(
            "updated_by_type IN ('user', 'service_account')", name=op.f("ck_skills_updated_by_type_valid")
        ),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 64", name=op.f("ck_skills_key_bounded")),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 256", name=op.f("ck_skills_name_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_skills_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_skills_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("id", "workspace_id", "organization_id", name="uq_skills_identity_scope"),
    )
    op.create_index("ix_skills_listing", "skills", ["workspace_id", "name", "id"], unique=False)
    op.create_index(
        "ix_skills_labels",
        "skills",
        ["labels"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"labels": "jsonb_path_ops"},
    )
    op.create_index(
        "uq_skills_workspace_key_active",
        "skills",
        ["workspace_id", "key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "skill_revisions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("skill_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("imported_from", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_skill_revisions_created_by_type_valid")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_skill_revisions_version_positive")),
        sa.ForeignKeyConstraint(
            ["skill_id", "workspace_id", "organization_id"],
            ["skills.id", "skills.workspace_id", "skills.organization_id"],
            name=op.f("fk_skill_revisions_skill_id_skills"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_revisions")),
        sa.UniqueConstraint(
            "id", "skill_id", "workspace_id", "organization_id", name="uq_skill_revisions_identity_scope"
        ),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_revisions_version_per_skill"),
    )
    op.create_index("ix_skill_revisions_digest", "skill_revisions", ["workspace_id", "content_digest"], unique=False)
    op.create_index("ix_skill_revisions_listing", "skill_revisions", ["skill_id", "version", "id"], unique=False)
    op.create_table(
        "skill_uploads",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("uploader_type", sa.String(length=32), nullable=False),
        sa.Column("uploader_id", sa.String(length=72), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_by_revision_id", sa.String(length=72), nullable=True),
        sa.CheckConstraint(
            "uploader_type IN ('user', 'service_account')", name=op.f("ck_skill_uploads_uploader_type_valid")
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by_revision_id"],
            ["skill_revisions.id"],
            name=op.f("fk_skill_uploads_consumed_by_revision_id_skill_revisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_skill_uploads_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_uploads")),
    )
    op.create_index("ix_skill_uploads_expiry", "skill_uploads", ["expires_at", "id"], unique=False)
    op.create_index(
        "ix_skill_uploads_principal",
        "skill_uploads",
        ["workspace_id", "uploader_type", "uploader_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index("ix_skill_uploads_principal", table_name="skill_uploads")
    op.drop_index("ix_skill_uploads_expiry", table_name="skill_uploads")
    op.drop_table("skill_uploads")
    op.drop_index("ix_skill_revisions_listing", table_name="skill_revisions")
    op.drop_index("ix_skill_revisions_digest", table_name="skill_revisions")
    op.drop_table("skill_revisions")
    op.drop_index("uq_skills_workspace_key_active", table_name="skills", postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_index("ix_skills_listing", table_name="skills")
    op.drop_index("ix_skills_labels", table_name="skills", postgresql_using="gin")
    op.drop_table("skills")
    op.drop_index(
        "uq_assets_run_invocation", table_name="assets", postgresql_where=sa.text("source_kind = 'run_output'")
    )
    op.drop_index(
        "ix_assets_tombstone_retention", table_name="assets", postgresql_where=sa.text("deleted_at IS NOT NULL")
    )
    op.drop_index(
        "ix_assets_active_workspace_listing", table_name="assets", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(
        "ix_assets_active_source_kind_listing", table_name="assets", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(
        "ix_assets_active_run_attempt_listing", table_name="assets", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_table("assets")
