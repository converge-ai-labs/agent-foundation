"""Relational authority for managed Environments and immutable Revisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    Environment,
    EnvironmentAccess,
    EnvironmentCredentialBinding,
    EnvironmentProviderLock,
    EnvironmentProviderSelection,
    EnvironmentRevision,
)

_LOCK_ADAPTER = TypeAdapter(EnvironmentProviderLock)
_BINDINGS_ADAPTER = TypeAdapter(tuple[EnvironmentCredentialBinding, ...])


class EnvironmentProviderSelectionRecord(Base):
    __tablename__ = "environment_provider_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        Index("ix_environment_provider_selections_enabled", "workspace_id", "enabled", "provider_key"),
    )

    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_package_revision_id: Mapped[str | None] = mapped_column(String(72))
    provider_lock: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> EnvironmentProviderSelection:
        return EnvironmentProviderSelection(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            provider_key=self.provider_key,
            provider_package_revision_id=self.provider_package_revision_id,
            provider_lock=_LOCK_ADAPTER.validate_python(self.provider_lock),
            enabled=self.enabled,
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            updated_at=_utc(self.updated_at),
        )


class EnvironmentRecord(Base):
    __tablename__ = "environments"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        Index("uq_environments_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("uq_environments_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_environments_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4096))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_revision_id: Mapped[str] = mapped_column(String(72), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    updated_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Environment:
        return Environment(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=self.name,
            description=self.description,
            version=self.version,
            current_revision_id=self.current_revision_id,
            archived_at=_utc(self.archived_at) if self.archived_at is not None else None,
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=_utc(self.created_at),
            updated_at=_utc(self.updated_at),
        )


class EnvironmentRevisionRecord(Base):
    __tablename__ = "environment_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("environment_id", "organization_id", "workspace_id"),
            ("environments.id", "environments.organization_id", "environments.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("access IN ('read_only', 'read_write', 'full')", name="access_valid"),
        CheckConstraint("length(logical_digest_sha256) = 64", name="logical_digest_sha256"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        UniqueConstraint("environment_id", "version", name="uq_environment_revisions_number"),
        UniqueConstraint("id", "environment_id", name="uq_environment_revisions_id_environment"),
        Index("uq_environment_revisions_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_environment_revisions_environment_desc", "environment_id", "version", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    environment_id: Mapped[str] = mapped_column(String(72), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider_package_revision_id: Mapped[str | None] = mapped_column(String(72))
    provider_lock: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    credential_bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    access: Mapped[str] = mapped_column(String(16), nullable=False)
    logical_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> EnvironmentRevision:
        from a13n_environment_provider import EnvironmentProviderSpec

        return EnvironmentRevision(
            id=self.id,
            environment_id=self.environment_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            version=self.version,
            provider=EnvironmentProviderSpec.model_validate(self.provider),
            provider_package_revision_id=self.provider_package_revision_id,
            provider_lock=_LOCK_ADAPTER.validate_python(self.provider_lock),
            credential_bindings=_BINDINGS_ADAPTER.validate_python(self.credential_bindings),
            access=EnvironmentAccess(self.access),
            logical_digest_sha256=self.logical_digest_sha256,
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=_utc(self.created_at),
        )


def _principal(value: str, principal_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(value), principal_id=principal_id)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
