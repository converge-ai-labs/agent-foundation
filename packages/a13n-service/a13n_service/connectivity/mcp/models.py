"""Relational Connection, OAuth session, and token-refresh coordination."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base

from ..connections.models import AuthorizationRecord, ConnectionRecord


class MCPConnectionRecord(ConnectionRecord):
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, str]:
        return {"polymorphic_identity": "mcp"}


class MCPAuthorizationRecord(AuthorizationRecord):
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, str]:
        return {"polymorphic_identity": "mcp"}


class MCPConnectionOAuthClientRecord(ResourceCredential[str], Base):
    """Connection-owned app registration, independent of its access token."""

    credential_owner_type = "mcp_oauth_client"
    __tablename__ = "mcp_oauth_clients"
    __table_args__ = (
        ForeignKeyConstraint(
            ("id", "organization_id", "workspace_id"),
            ("connections.id", "connections.organization_id", "connections.workspace_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR "
            "(ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        CheckConstraint("credential_generation >= 0", name="credential_generation_non_negative"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    configuration_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
