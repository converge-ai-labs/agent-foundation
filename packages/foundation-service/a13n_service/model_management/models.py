"""Relational ModelConfig rows."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType

from .domain import (
    CapabilitySource,
    ModelCapabilities,
    ModelConfigResource,
    ModelCredential,
)

_CREDENTIAL_ADAPTER = TypeAdapter(ModelCredential)


class ModelConfigRecord(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("length(model_name) BETWEEN 1 AND 256", name="model_name_bounded"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("capability_source IN ('catalog', 'manual_override')", name="capability_source_valid"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        Index("uq_model_configs_workspace_normalized_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_model_configs_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_model_configs_workspace_provider", "workspace_id", "provider_type", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(2048))
    provider_type: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(256))
    base_url: Mapped[str | None] = mapped_column(String(2048))
    credential: Mapped[dict[str, object]] = mapped_column(JSON)
    provider_config: Mapped[dict[str, object]] = mapped_column(JSON)
    capabilities: Mapped[dict[str, object]] = mapped_column(JSON)
    capability_source: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    updated_by_type: Mapped[str] = mapped_column(String(32))
    updated_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> ModelConfigResource:
        return ModelConfigResource(
            id=self.id,
            workspace_id=self.workspace_id,
            version=self.version,
            name=self.name,
            description=self.description,
            provider_type=self.provider_type,
            model_name=self.model_name,
            base_url=self.base_url,
            credential=_CREDENTIAL_ADAPTER.validate_python(self.credential),
            provider_config=self.provider_config,
            capabilities=ModelCapabilities.model_validate(self.capabilities),
            capability_source=CapabilitySource(self.capability_source),
            enabled=self.enabled,
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type), principal_id=self.created_by_id
            ),
            updated_by=PrincipalRef(
                principal_type=PrincipalType(self.updated_by_type), principal_id=self.updated_by_id
            ),
            created_at=_as_utc(self.created_at),
            updated_at=_as_utc(self.updated_at),
        )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
