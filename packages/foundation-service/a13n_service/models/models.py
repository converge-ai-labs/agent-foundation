"""Relational Model heads and immutable revisions."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType

from .domain import CapabilitySource, Model, ModelCapabilities, ModelCredential, ModelRevision

_CREDENTIAL_ADAPTER = TypeAdapter(ModelCredential)


class ModelRecord(Base):
    __tablename__ = "models"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        Index("uq_models_identity_scope", "id", "workspace_id", "organization_id", unique=True),
        Index("uq_models_workspace_normalized_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_models_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(2048))
    version: Mapped[int] = mapped_column(BigInteger)
    current_revision_id: Mapped[str] = mapped_column(String(72))
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    updated_by_type: Mapped[str] = mapped_column(String(32))
    updated_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> Model:
        return Model(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=self.name,
            description=self.description,
            version=self.version,
            current_revision_id=self.current_revision_id,
            enabled=self.enabled,
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=_as_utc(self.created_at),
            updated_at=_as_utc(self.updated_at),
        )


class ModelRevisionRecord(Base):
    __tablename__ = "model_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("model_id", "workspace_id", "organization_id"),
            ("models.id", "models.workspace_id", "models.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(model_name) BETWEEN 1 AND 256", name="model_name_bounded"),
        CheckConstraint("capability_source IN ('catalog', 'manual_override')", name="capability_source_valid"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("uq_model_revisions_model_version", "model_id", "version", unique=True),
        Index("ix_model_revisions_listing", "model_id", "version", "id"),
        Index("ix_model_revisions_workspace_provider", "workspace_id", "provider_type", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    model_id: Mapped[str] = mapped_column(String(72))
    version: Mapped[int] = mapped_column(BigInteger)
    provider_type: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(256))
    base_url: Mapped[str | None] = mapped_column(String(2048))
    credential: Mapped[dict[str, object]] = mapped_column(JSON)
    provider_config: Mapped[dict[str, object]] = mapped_column(JSON)
    capabilities: Mapped[dict[str, object]] = mapped_column(JSON)
    capability_source: Mapped[str] = mapped_column(String(32))
    content_digest: Mapped[str] = mapped_column(String(64))
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> ModelRevision:
        return ModelRevision(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            model_id=self.model_id,
            version=self.version,
            provider_type=self.provider_type,
            model_name=self.model_name,
            base_url=self.base_url,
            credential=_CREDENTIAL_ADAPTER.validate_python(self.credential),
            provider_config=self.provider_config,
            capabilities=ModelCapabilities.model_validate(self.capabilities),
            capability_source=CapabilitySource(self.capability_source),
            content_digest=self.content_digest,
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=_as_utc(self.created_at),
        )


def _principal(principal_type: str, principal_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(principal_type), principal_id=principal_id)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
