"""Relational records for managed Skills, revisions, uploads, and replay evidence."""

from __future__ import annotations

from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.temporal import assume_utc, optional_assume_utc

from .domain import (
    Skill,
    SkillImportProvenance,
    SkillPackageManifest,
    SkillRevision,
    SkillUploadReceipt,
)

_PROVENANCE_ADAPTER = TypeAdapter(SkillImportProvenance)


class SkillRecord(Base):
    __tablename__ = "skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "workspace_id", "organization_id", name="uq_skills_identity_scope"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(key) BETWEEN 1 AND 64", name="key_bounded"),
        CheckConstraint("length(name) BETWEEN 1 AND 256", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        Index("ix_skills_listing", "workspace_id", "name", "id"),
        Index(
            "uq_skills_workspace_key_active",
            "workspace_id",
            "key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(BigInteger)
    current_revision_id: Mapped[str] = mapped_column(String(72))
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    updated_by_type: Mapped[str] = mapped_column(String(32))
    updated_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> Skill:
        return Skill(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            key=self.key,
            name=self.name,
            version=self.version,
            current_revision_id=self.current_revision_id,
            created_at=assume_utc(self.created_at),
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
            updated_by=PrincipalRef(
                principal_type=PrincipalType(self.updated_by_type),
                principal_id=self.updated_by_id,
            ),
            updated_at=assume_utc(self.updated_at),
            deleted_at=optional_assume_utc(self.deleted_at),
        )


class SkillRevisionRecord(Base):
    __tablename__ = "skill_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("skill_id", "workspace_id", "organization_id"),
            ("skills.id", "skills.workspace_id", "skills.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "skill_id",
            "workspace_id",
            "organization_id",
            name="uq_skill_revisions_identity_scope",
        ),
        UniqueConstraint(
            "skill_id",
            "version",
            name="uq_skill_revisions_version_per_skill",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("ix_skill_revisions_listing", "skill_id", "version", "id"),
        Index("ix_skill_revisions_digest", "workspace_id", "content_digest"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    skill_id: Mapped[str] = mapped_column(String(72))
    version: Mapped[int] = mapped_column(BigInteger)
    content_digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, object]] = mapped_column(JSON)
    imported_from: Mapped[dict[str, object]] = mapped_column(JSON)
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> SkillRevision:
        return SkillRevision(
            id=self.id,
            skill_id=self.skill_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            version=self.version,
            manifest=SkillPackageManifest.model_validate(self.manifest),
            imported_from=_PROVENANCE_ADAPTER.validate_python(self.imported_from),
            created_at=assume_utc(self.created_at),
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
        )


class SkillUploadRecord(Base):
    __tablename__ = "skill_uploads"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("consumed_by_revision_id",),
            ("skill_revisions.id",),
            ondelete="RESTRICT",
        ),
        CheckConstraint("uploader_type IN ('user', 'service_account')", name="uploader_type_valid"),
        Index("ix_skill_uploads_expiry", "expires_at", "id"),
        Index("ix_skill_uploads_principal", "workspace_id", "uploader_type", "uploader_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    uploader_type: Mapped[str] = mapped_column(String(32))
    uploader_id: Mapped[str] = mapped_column(String(72))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_by_revision_id: Mapped[str | None] = mapped_column(String(72))

    def to_resource(self) -> SkillUploadReceipt:
        return SkillUploadReceipt(
            upload_id=self.id,
            workspace_id=self.workspace_id,
            archive_sha256=self.archive_sha256,
            manifest=SkillPackageManifest.model_validate(self.manifest),
            expires_at=assume_utc(self.expires_at),
            consumed_by_revision_id=self.consumed_by_revision_id,
        )


class SkillIdempotencyRecord(Base):
    __tablename__ = "skill_idempotency"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "operation",
            "workspace_id",
            "actor_type",
            "actor_id",
            "scope_id",
            "key_digest",
            name="uq_skill_idempotency_replay_scope",
        ),
        CheckConstraint("actor_type IN ('user', 'service_account')", name="actor_type_valid"),
        CheckConstraint("status_code IN (200, 201)", name="status_code_valid"),
        Index("ix_skill_idempotency_expiry", "expires_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(72))
    operation: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str] = mapped_column(String(72))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_body: Mapped[dict[str, object]] = mapped_column(JSON)
    status_code: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
