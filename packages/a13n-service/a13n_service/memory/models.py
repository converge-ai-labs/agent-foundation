"""Relational Memory Provider accounts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.names import CASEFOLDED_NAME_MAX_LENGTH
from a13n_service.temporal import assume_utc

from .domain import MemoryProvider


class MemoryStorageRecord(Base):
    """Exact file corpus identity; no document bodies are retained in SQL."""

    __tablename__ = "memory_storage_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"), ("workspaces.id", "workspaces.organization_id"), ondelete="CASCADE"
        ),
        Index("uq_memory_storage_target", "target_digest", unique=True),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    target_digest: Mapped[str] = mapped_column(String(64))
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    provider_identity: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(128))
    scope_kind: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(String(72))
    environment_id: Mapped[str] = mapped_column(String(72))
    root: Mapped[str] = mapped_column(Text)
    backing_identity: Mapped[str] = mapped_column(String(256))
    initialized: Mapped[bool] = mapped_column(Boolean, default=False)


class RunMemoryStorageRecord(Base):
    __tablename__ = "run_memory_storage_bindings"

    run_id: Mapped[str] = mapped_column(String(72), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    selection_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_policy: Mapped[dict[str, object] | None] = mapped_column(JSON)
    storage_id: Mapped[str] = mapped_column(String(72), ForeignKey("memory_storage_bindings.id", ondelete="RESTRICT"))


class MemoryProviderRecord(ResourceCredential[str | None], Base):
    credential_owner_type = "memory_provider"
    __tablename__ = "memory_providers"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        Index(
            "uq_memory_providers_organization_normalized_name",
            "organization_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("workspace_id IS NULL"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("credential_generation >= 0", name="credential_generation_nonnegative"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR "
            "(ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        Index("uq_memory_providers_identity_scope", "id", "organization_id", unique=True),
        Index("uq_memory_providers_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_memory_providers_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    type: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(CASEFOLDED_NAME_MAX_LENGTH))
    configuration: Mapped[dict[str, object]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    updated_by_type: Mapped[str] = mapped_column(String(32))
    updated_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> MemoryProvider:
        return MemoryProvider(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            type=self.type,
            name=self.name,
            configuration=self.configuration,
            credential_configured=self.ciphertext is not None,
            enabled=self.enabled,
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


def _principal(principal_type: str, principal_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(principal_type), principal_id=principal_id)


class MemoryOrganizationRecord(Base):
    __tablename__ = "memory_organization_work"
    __table_args__ = (Index("ix_memory_organization_pending", "status", "available_at"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(72), ForeignKey("runs.id", ondelete="CASCADE"))
    storage_id: Mapped[str] = mapped_column(String(72), ForeignKey("memory_storage_bindings.id", ondelete="RESTRICT"))
    policy: Mapped[dict[str, object]] = mapped_column(JSON)
    plan_digest: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    owner: Mapped[str | None] = mapped_column(String(72))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
