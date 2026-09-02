"""Relational authority for Agent and immutable Revision resources."""

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
    Agent,
    AgentConfig,
    AgentRevision,
    AgentSource,
    EnvironmentExecutionConfig,
    PluginRuntimeMode,
    ResolvedAgentModel,
    ResolvedPluginVersion,
    ResolvedSkillSelection,
    ResolvedSubagentEdge,
)

_CONFIG_ADAPTER = TypeAdapter(AgentConfig)
_MODEL_ADAPTER = TypeAdapter(ResolvedAgentModel)
_PLUGINS_ADAPTER = TypeAdapter(tuple[ResolvedPluginVersion, ...])
_SKILLS_ADAPTER = TypeAdapter(tuple[ResolvedSkillSelection, ...])
_ENVIRONMENT_ADAPTER = TypeAdapter(EnvironmentExecutionConfig | None)
_SUBAGENTS_ADAPTER = TypeAdapter(tuple[ResolvedSubagentEdge, ...])


class AgentRecord(Base):
    __tablename__ = "agents"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("source IN ('builtin', 'custom')", name="source_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account', 'system')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account', 'system')", name="updated_by_type_valid"),
        Index("uq_agents_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("uq_agents_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_agents_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_agents_workspace_availability", "workspace_id", "enabled", "archived_at", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4096))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_revision_id: Mapped[str] = mapped_column(String(72), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duplicated_from_agent_id: Mapped[str | None] = mapped_column(String(72))
    duplicated_from_revision_id: Mapped[str | None] = mapped_column(String(72))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    updated_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Agent:
        return Agent(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            source=AgentSource(self.source),
            name=self.name,
            description=self.description,
            version=self.version,
            current_revision_id=self.current_revision_id,
            enabled=self.enabled,
            archived_at=_optional_utc(self.archived_at),
            duplicated_from_agent_id=self.duplicated_from_agent_id,
            duplicated_from_revision_id=self.duplicated_from_revision_id,
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=_as_utc(self.created_at),
            updated_at=_as_utc(self.updated_at),
        )


class AgentRevisionRecord(Base):
    __tablename__ = "agent_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("plugin_runtime_mode IN ('on_demand', 'runner')", name="plugin_runtime_mode_valid"),
        CheckConstraint("length(config_digest) = 64", name="config_digest_sha256"),
        CheckConstraint("length(runtime_lock_digest) = 64", name="runtime_lock_digest_sha256"),
        CheckConstraint("length(content_digest) = 64", name="content_digest_sha256"),
        CheckConstraint("created_by_type IN ('user', 'service_account', 'system')", name="created_by_type_valid"),
        UniqueConstraint("agent_id", "version", name="uq_agent_revisions_agent_number"),
        Index("uq_agent_revisions_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index(
            "ix_agent_revisions_agent_desc",
            "agent_id",
            "version",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plugin_runtime_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_model: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolved_plugin_versions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    runtime_lock_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_skills: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    resolved_environment: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    resolved_subagents: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision_id: Mapped[str | None] = mapped_column(String(72))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> AgentRevision:
        return AgentRevision(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            agent_id=self.agent_id,
            version=self.version,
            plugin_runtime_mode=PluginRuntimeMode(self.plugin_runtime_mode),
            config=_CONFIG_ADAPTER.validate_python(self.config),
            resolved_model=_MODEL_ADAPTER.validate_python(self.resolved_model),
            resolved_plugin_versions=_PLUGINS_ADAPTER.validate_python(self.resolved_plugin_versions),
            runtime_lock_digest=self.runtime_lock_digest,
            resolved_skills=_SKILLS_ADAPTER.validate_python(self.resolved_skills),
            resolved_environment=_ENVIRONMENT_ADAPTER.validate_python(self.resolved_environment),
            resolved_subagents=_SUBAGENTS_ADAPTER.validate_python(self.resolved_subagents),
            content_digest=self.content_digest,
            source_revision_id=self.source_revision_id,
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=_as_utc(self.created_at),
        )


def _principal(principal_type: str, principal_id: str) -> PrincipalRef:
    if principal_type == "system":
        # System-authored built-ins use a stable service-account-shaped public actor
        # until the shared IAM domain exposes SystemActorRef.
        return PrincipalRef(principal_type=PrincipalType.service_account, principal_id=principal_id)
    return PrincipalRef(principal_type=PrincipalType(principal_type), principal_id=principal_id)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)
