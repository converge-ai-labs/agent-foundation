"""Durable Provider and verified Connection facts, with temporary setup attempts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.names import CASEFOLDED_NAME_MAX_LENGTH
from a13n_service.temporal import assume_utc

from ..connections.models import AuthorizationRecord, ConnectionRecord
from .domain import ConnectorProvider, ConnectorProviderStatus


class ConnectorProviderRecord(ResourceCredential[str | None], Base):
    credential_owner_type = "connector_provider"
    __tablename__ = "connector_providers"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        Index(
            "uq_connector_providers_organization_name",
            "organization_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("workspace_id IS NULL"),
        ),
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR "
            "(ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("credential_generation >= 1", name="credential_generation_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("uq_connector_providers_id_organization", "id", "organization_id", unique=True),
        Index("uq_connector_providers_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_connector_providers_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_connector_providers_driver_status", "type", "status", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(CASEFOLDED_NAME_MAX_LENGTH), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    directory_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    directory_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> ConnectorProvider:
        return ConnectorProvider(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=self.name,
            type=self.type,
            configuration=self.configuration_json,
            status=ConnectorProviderStatus(self.status),
            version=self.version,
            credential_configured=self.ciphertext is not None,
            credential_generation=self.credential_generation,
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class ConnectorSharedSetupClaimRecord(Base):
    __tablename__ = "connector_shared_setup_claims"
    __table_args__ = (
        ForeignKeyConstraint(("provider_id",), ("connector_providers.id",), ondelete="CASCADE"),
        CheckConstraint("credential_generation >= 1", name="credential_generation_positive"),
        UniqueConstraint(
            "provider_id",
            "connector_key",
            "configuration_key",
            name="uq_connector_shared_setup_claims_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(72), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    configuration_key: Mapped[str] = mapped_column(String(128), nullable=False)
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ConnectorConnectionRecord(ConnectionRecord):
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, str]:
        return {"polymorphic_identity": "connector"}


class ConnectorAuthorizationRecord(AuthorizationRecord):
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, str]:
        return {"polymorphic_identity": "connector"}


def _principal(kind: str, identifier: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(kind), principal_id=identifier)
