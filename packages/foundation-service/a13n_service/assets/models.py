"""Relational authority for immutable Assets."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.temporal import assume_utc, optional_assume_utc

from .domain import Asset, RunOutputAssetSource, UploadedAssetSource


class AssetRecord(Base):
    __tablename__ = "assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("length(filename) BETWEEN 1 AND 256", name="filename_bounded"),
        CheckConstraint("filename = trim(filename)", name="filename_trimmed"),
        CheckConstraint("length(media_type) BETWEEN 3 AND 255", name="media_type_bounded"),
        CheckConstraint("media_type = lower(media_type)", name="media_type_lowercase"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256_length"),
        CheckConstraint("content_sha256 = lower(content_sha256)", name="content_sha256_lowercase"),
        CheckConstraint("source_kind IN ('upload', 'run_output')", name="source_kind_valid"),
        CheckConstraint(
            "source_principal_type IS NULL OR source_principal_type IN ('user', 'service_account')",
            name="source_principal_type_valid",
        ),
        CheckConstraint(
            "(source_kind = 'upload' AND source_principal_type IS NOT NULL "
            "AND source_principal_id IS NOT NULL AND source_run_attempt_id IS NULL "
            "AND source_invocation_id IS NULL) OR "
            "(source_kind = 'run_output' AND source_principal_type IS NULL "
            "AND source_principal_id IS NULL AND source_run_attempt_id IS NOT NULL "
            "AND source_invocation_id IS NOT NULL)",
            name="source_shape_valid",
        ),
        CheckConstraint("deleted_at IS NULL OR deleted_at >= created_at", name="deletion_after_creation"),
        Index(
            "ix_assets_active_workspace_listing",
            "organization_id",
            "workspace_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_assets_active_source_kind_listing",
            "organization_id",
            "workspace_id",
            "source_kind",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_assets_active_run_attempt_listing",
            "organization_id",
            "workspace_id",
            "source_run_attempt_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_assets_run_invocation",
            "source_run_attempt_id",
            "source_invocation_id",
            unique=True,
            postgresql_where=text("source_kind = 'run_output'"),
            sqlite_where=text("source_kind = 'run_output'"),
        ),
        Index(
            "ix_assets_tombstone_retention",
            "deleted_at",
            "id",
            postgresql_where=text("deleted_at IS NOT NULL"),
            sqlite_where=text("deleted_at IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    filename: Mapped[str] = mapped_column(String(1024))
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_sha256: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(32))
    source_principal_type: Mapped[str | None] = mapped_column(String(32))
    source_principal_id: Mapped[str | None] = mapped_column(String(72))
    source_run_attempt_id: Mapped[str | None] = mapped_column(String(72))
    source_invocation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self, *, source_run_id: str | None = None) -> Asset:
        if self.source_kind == "upload":
            assert self.source_principal_type is not None
            assert self.source_principal_id is not None
            source = UploadedAssetSource(
                principal=PrincipalRef(
                    principal_type=PrincipalType(self.source_principal_type),
                    principal_id=self.source_principal_id,
                )
            )
        else:
            if source_run_id is None:
                raise ValueError("run-output Asset projection requires its source Run ID")
            source = RunOutputAssetSource(run_id=source_run_id)
        return Asset(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            filename=self.filename,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            content_sha256=self.content_sha256,
            source=source,
            created_at=assume_utc(self.created_at),
            deleted_at=optional_assume_utc(self.deleted_at),
        )
