"""Relational Model Provider and Model resources."""

from __future__ import annotations

from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.names import CASEFOLDED_NAME_MAX_LENGTH
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.temporal import assume_utc

from .credentials import ProviderSecrets
from .domain import Model, ModelDeclarations, ModelProvider


class ModelProviderRecord(ResourceCredential[str | None], Base):
    credential_owner_type = "model_provider"
    __tablename__ = "model_providers"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        Index(
            "uq_model_providers_organization_normalized_name",
            "organization_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("workspace_id IS NULL"),
            sqlite_where=text("workspace_id IS NULL"),
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
        Index("uq_model_providers_identity_scope", "id", "organization_id", unique=True),
        Index("uq_model_providers_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_model_providers_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    type: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(CASEFOLDED_NAME_MAX_LENGTH))
    configuration: Mapped[dict[str, object]] = mapped_column(JSON)
    credential_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    header_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_by_type: Mapped[str] = mapped_column(String(32))
    created_by_id: Mapped[str] = mapped_column(String(72))
    updated_by_type: Mapped[str] = mapped_column(String(32))
    updated_by_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def replace_secrets(self, value: ProviderSecrets, protector: SecretProtector) -> None:
        self.replace_credential(value.encrypted_value(), protector)
        self.credential_configured = value.credential is not None
        self.header_names = sorted(value.extra_headers)

    def to_resource(self) -> ModelProvider:
        return ModelProvider(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            type=self.type,
            name=self.name,
            configuration=self.configuration,
            credential_configured=self.credential_configured,
            header_names=tuple(self.header_names),
            enabled=self.enabled,
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class ModelRecord(Base):
    __tablename__ = "models"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        Index(
            "uq_models_organization_normalized_key",
            "organization_id",
            "normalized_key",
            unique=True,
            postgresql_where=text("workspace_id IS NULL"),
            sqlite_where=text("workspace_id IS NULL"),
        ),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("provider_id", "organization_id"),
            ("model_providers.id", "model_providers.organization_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(key) BETWEEN 1 AND 128", name="key_bounded"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("length(upstream_model) BETWEEN 1 AND 256", name="upstream_model_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        Index("uq_models_identity_scope", "id", "organization_id", unique=True),
        Index("uq_models_workspace_key", "workspace_id", "normalized_key", unique=True),
        Index("ix_models_provider", "provider_id", "id"),
        Index("ix_models_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    key: Mapped[str] = mapped_column(String(128))
    normalized_key: Mapped[str] = mapped_column(String(128))
    provider_id: Mapped[str] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(2048))
    upstream_model: Mapped[str] = mapped_column(String(256))
    model_api: Mapped[str] = mapped_column(String(96))
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    declarations: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
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
            key=self.key,
            provider_id=self.provider_id,
            name=self.name,
            description=self.description,
            upstream_model=self.upstream_model,
            model_api=self.model_api,
            settings=self.settings,
            declarations=ModelDeclarations.model_validate(self.declarations),
            enabled=self.enabled,
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


def _principal(principal_type: str, principal_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(principal_type), principal_id=principal_id)
