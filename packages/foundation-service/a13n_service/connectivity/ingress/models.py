"""Relational Ingress and Route resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.agents.domain import AgentRunOverride
from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.temporal import assume_utc

from .domain import Ingress, IngressStatus, InputBatchingPolicy, Route

_OVERLAYS = TypeAdapter(dict[str, AgentRunOverride])


class IngressRecord(Base):
    __tablename__ = "ingresses"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("default_agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("credential_generation >= 1", name="credential_generation_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("uq_ingresses_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("uq_ingresses_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_ingresses_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_ingresses_provider_status", "provider_key", "status", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    execution_service_account_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("service_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    default_agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credential_secret_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("secrets.id", ondelete="RESTRICT"), nullable=False
    )
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self, agents: tuple[str, ...]) -> Ingress:
        return Ingress(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=self.name,
            provider_key=self.provider_key,
            provider_config_version=self.provider_config_version,
            provider_config=self.provider_config_json,
            execution_principal_ref=PrincipalRef(
                principal_type=PrincipalType.service_account,
                principal_id=self.execution_service_account_id,
            ),
            agents=agents,
            default_agent_id=self.default_agent_id,
            status=IngressStatus(self.status),
            version=self.version,
            credential_configured=True,
            credential_generation=self.credential_generation,
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class IngressAgentRecord(Base):
    __tablename__ = "ingress_agents"
    __table_args__ = (
        ForeignKeyConstraint(
            ("ingress_id", "organization_id", "workspace_id"),
            ("ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        Index("ix_ingress_agents_agent", "workspace_id", "agent_id", "ingress_id"),
    )

    ingress_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)


class RouteRecord(Base):
    __tablename__ = "ingress_routes"
    __table_args__ = (
        ForeignKeyConstraint(
            ("ingress_id", "organization_id", "workspace_id"),
            ("ingresses.id", "ingresses.organization_id", "ingresses.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("min_interval_ms >= 1", name="min_interval_positive"),
        CheckConstraint("max_batch_events >= 1", name="max_batch_events_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        UniqueConstraint("ingress_id", "normalized_name", name="uq_ingress_routes_ingress_name"),
        Index("uq_ingress_routes_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_ingress_routes_listing", "ingress_id", "enabled", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    ingress_id: Mapped[str] = mapped_column(String(72), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    match_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(72))
    input_mapping_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    min_interval_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_batch_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capability_overlays_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Route:
        return Route(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            ingress_id=self.ingress_id,
            name=self.name,
            provider_config_version=self.provider_config_version,
            match=self.match_json,
            agent_id=self.agent_id,
            input_mapping=self.input_mapping_json,
            input_batching=InputBatchingPolicy(
                min_interval_ms=self.min_interval_ms,
                max_batch_events=self.max_batch_events,
            ),
            capability_overlays=_OVERLAYS.validate_python(self.capability_overlays_json),
            provider_policy=self.provider_policy_json,
            enabled=self.enabled,
            version=self.version,
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )
