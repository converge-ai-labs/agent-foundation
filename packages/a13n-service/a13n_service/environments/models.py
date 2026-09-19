"""Workspace-owned provider credentials, template configurations, and target lifecycle authority."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import JsonValue
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base
from a13n_service.labels import LABELS_SQL_TYPE
from a13n_service.temporal import assume_utc

from .domain import (
    Environment,
    EnvironmentCommand,
    EnvironmentProviderAccount,
    EnvironmentTemplate,
    EnvironmentTemplateRevision,
)


class ResourceColumns[WorkspaceId: str | None]:
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[WorkspaceId]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def identity(self) -> dict[str, str | datetime | None]:
        return dict(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class EnvironmentProviderRecord(ResourceCredential[str | None], ResourceColumns[str | None], Base):
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    __tablename__ = "environment_providers"
    credential_owner_type: ClassVar[str] = "environment_provider"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"), ("workspaces.id", "workspaces.organization_id"), ondelete="CASCADE"
        ),
        UniqueConstraint("id", "organization_id", name="uq_environment_providers_scope"),
        CheckConstraint("configuration_source IN ('user', 'deployment')", name="configuration_source_valid"),
        CheckConstraint("configuration_source != 'deployment' OR workspace_id IS NULL", name="deployment_organization"),
        Index("ix_environment_providers_workspace", "workspace_id", "id"),
    )
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    configuration: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration_source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")

    def to_resource(self) -> EnvironmentProviderAccount:
        return EnvironmentProviderAccount.model_validate(
            {
                **self.identity(),
                "type": self.type,
                "name": self.name,
                "configuration": self.configuration,
                "enabled": self.enabled,
                "credential_configured": self.ciphertext is not None,
                "configuration_source": self.configuration_source,
            }
        )


class EnvironmentTemplateRecord(ResourceColumns[str | None], Base):
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    __tablename__ = "environment_templates"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"), ("workspaces.id", "workspaces.organization_id"), ondelete="CASCADE"
        ),
        UniqueConstraint("id", "organization_id", name="uq_environment_templates_scope"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_environment_templates_workspace", "workspace_id", "id"),
        Index(
            "ix_environment_templates_labels",
            "labels",
            postgresql_using="gin",
            postgresql_ops={"labels": "jsonb_path_ops"},
        ),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4096))
    labels: Mapped[dict[str, str]] = mapped_column(
        LABELS_SQL_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_revision_id: Mapped[str] = mapped_column(String(72), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> EnvironmentTemplate:
        return EnvironmentTemplate.model_validate(
            {
                **self.identity(),
                "name": self.name,
                "description": self.description,
                "labels": self.labels,
                "version": self.version,
                "current_revision_id": self.current_revision_id,
                "archived_at": assume_utc(self.archived_at) if self.archived_at else None,
            }
        )


class EnvironmentTemplateRevisionRecord(Base):
    __tablename__ = "environment_template_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("template_id", "organization_id"), ("environment_templates.id", "environment_templates.organization_id")
        ),
        ForeignKeyConstraint(
            ("provider_id", "organization_id"), ("environment_providers.id", "environment_providers.organization_id")
        ),
        UniqueConstraint("template_id", "version", name="uq_environment_template_revisions_version"),
        UniqueConstraint("id", "provider_id", "organization_id", name="uq_environment_template_revisions_provider"),
        CheckConstraint("version >= 1", name="version_positive"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(72), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    provider_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    template_config: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> EnvironmentTemplateRevision:
        return EnvironmentTemplateRevision.model_validate(
            {
                **self.template_config,
                "id": self.id,
                "template_id": self.template_id,
                "organization_id": self.organization_id,
                "workspace_id": self.workspace_id,
                "provider_id": self.provider_id,
                "version": self.version,
                "created_at": assume_utc(self.created_at),
            }
        )


class EnvironmentRecord(ResourceColumns[str], Base):
    __tablename__ = "environments"
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"), ("workspaces.id", "workspaces.organization_id"), ondelete="CASCADE"
        ),
        ForeignKeyConstraint(("organization_id",), ("organizations.id",), ondelete="CASCADE"),
        ForeignKeyConstraint(
            ("provider_id", "organization_id"), ("environment_providers.id", "environment_providers.organization_id")
        ),
        ForeignKeyConstraint(
            ("template_revision_id", "provider_id", "organization_id"),
            (
                "environment_template_revisions.id",
                "environment_template_revisions.provider_id",
                "environment_template_revisions.organization_id",
            ),
        ),
        UniqueConstraint("id", "workspace_id", name="uq_environments_scope"),
        UniqueConstraint("target_identity", name="uq_environments_provider_target"),
        CheckConstraint("generation >= 0 AND operation_generation >= 0", name="generation_nonnegative"),
        CheckConstraint(
            "(ownership = 'managed' AND template_revision_id IS NOT NULL) OR (ownership = 'external' AND template_revision_id IS NULL)",
            name="ownership_template_config",
        ),
        CheckConstraint("status IN ('unprepared','running','stopped','deleted','unavailable')", name="status_valid"),
        CheckConstraint("retention_condition IN ('active','idle')", name="retention_condition_valid"),
        Index("ix_environments_maintenance", "next_maintenance_at", "id"),
        Index("ix_environments_capacity", "workspace_id", "ownership", "status"),
        Index(
            "ix_environments_labels",
            "labels",
            postgresql_using="gin",
            postgresql_ops={"labels": "jsonb_path_ops"},
        ),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="Environment")
    labels: Mapped[dict[str, str]] = mapped_column(
        LABELS_SQL_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    provider_id: Mapped[str] = mapped_column(String(72), nullable=False)
    template_revision_id: Mapped[str | None] = mapped_column(String(72))
    ownership: Mapped[str] = mapped_column(String(16), nullable=False)
    access: Mapped[str] = mapped_column(String(16), nullable=False)
    external_configuration: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)
    state: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)
    target_identity: Mapped[str | None] = mapped_column(String(256))
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unprepared")
    retention_condition: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    condition_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operation_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    operation_id: Mapped[str | None] = mapped_column(String(72))
    operation_action: Mapped[str | None] = mapped_column(String(16))
    operation_owner: Mapped[str | None] = mapped_column(String(72))
    operation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_maintenance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)

    def to_resource(self) -> Environment:
        return Environment.model_validate(
            {
                **self.identity(),
                "name": self.name,
                "labels": self.labels,
                "provider_id": self.provider_id,
                "template_revision_id": self.template_revision_id,
                "ownership": self.ownership,
                "access": self.access,
                "generation": self.generation,
                "status": self.status,
                "retention_condition": self.retention_condition,
                "condition_since": assume_utc(self.condition_since),
            }
        )


class EnvironmentCommandRecord(Base):
    """A retained receipt for an explicit target lifecycle request."""

    __tablename__ = "environment_commands"
    __table_args__ = (
        ForeignKeyConstraint(("environment_id",), ("environments.id",)),
        CheckConstraint("action IN ('stop', 'delete')", name="action_valid"),
        CheckConstraint("status IN ('pending', 'completed', 'failed')", name="status_valid"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    environment_id: Mapped[str] = mapped_column(String(72), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> EnvironmentCommand:
        return EnvironmentCommand.model_validate(
            dict(
                id=self.id,
                environment_id=self.environment_id,
                action=self.action,
                status=self.status,
                created_at=assume_utc(self.created_at),
                completed_at=assume_utc(self.completed_at) if self.completed_at else None,
            )
        )
