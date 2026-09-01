"""Relational records for managed Skills, revisions, uploads, and replay evidence."""

from __future__ import annotations

from datetime import UTC, datetime

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
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType

from .domain import (
    Skill,
    SkillImportProvenance,
    SkillPackageManifest,
    SkillRevision,
    SkillUploadReceipt,
)

_PROVENANCE_ADAPTER = TypeAdapter(SkillImportProvenance)


class SkillRecord(Base):
    __tablename__ = "workspace_skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "workspace_id", "organization_id", name="uq_workspace_skills_identity_scope"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 256", name="display_name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("ix_workspace_skills_listing", "workspace_id", "display_name", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    display_name: Mapped[str] = mapped_column(String(1024))
    version: Mapped[int] = mapped_column(BigInteger)
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self, *, current_revision_id: str) -> Skill:
        return Skill(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            display_name=self.display_name,
            version=self.version,
            current_revision_id=current_revision_id,
            created_at=_as_utc(self.created_at),
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
            updated_at=_as_utc(self.updated_at),
            deleted_at=_optional_utc(self.deleted_at),
        )


class SkillRevisionRecord(Base):
    __tablename__ = "workspace_skill_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("skill_id", "workspace_id", "organization_id"),
            ("workspace_skills.id", "workspace_skills.workspace_id", "workspace_skills.organization_id"),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "skill_id",
            "workspace_id",
            "organization_id",
            name="uq_workspace_skill_revisions_identity_scope",
        ),
        UniqueConstraint(
            "skill_id",
            "revision_number",
            name="uq_workspace_skill_revisions_number_per_skill",
        ),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("ix_workspace_skill_revisions_listing", "skill_id", "revision_number", "id"),
        Index("ix_workspace_skill_revisions_digest", "workspace_id", "content_digest"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    skill_id: Mapped[str] = mapped_column(String(72))
    revision_number: Mapped[int] = mapped_column(BigInteger)
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
            workspace_id=self.workspace_id,
            revision_number=self.revision_number,
            manifest=SkillPackageManifest.model_validate(self.manifest),
            imported_from=_PROVENANCE_ADAPTER.validate_python(self.imported_from),
            created_at=_as_utc(self.created_at),
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
        )


class SkillHeadRecord(Base):
    __tablename__ = "workspace_skill_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ("skill_id", "workspace_id", "organization_id"),
            ("workspace_skills.id", "workspace_skills.workspace_id", "workspace_skills.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("current_revision_id", "skill_id", "workspace_id", "organization_id"),
            (
                "workspace_skill_revisions.id",
                "workspace_skill_revisions.skill_id",
                "workspace_skill_revisions.workspace_id",
                "workspace_skill_revisions.organization_id",
            ),
            ondelete="RESTRICT",
        ),
    )

    skill_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    current_revision_id: Mapped[str] = mapped_column(String(72))


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
            ("workspace_skill_revisions.id",),
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
            expires_at=_as_utc(self.expires_at),
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


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)
