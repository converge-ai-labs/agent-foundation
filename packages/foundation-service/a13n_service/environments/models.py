"""Relational authority for managed Environments and immutable Revisions."""

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
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.temporal import assume_utc

from .domain import (
    Environment,
    EnvironmentAccess,
    EnvironmentConnectionSpec,
    EnvironmentCredentialBinding,
    EnvironmentProviderLock,
    EnvironmentProviderSelection,
    EnvironmentRevision,
    EnvironmentTarget,
    EnvironmentTargetRetentionBehavior,
    EnvironmentTargetStatus,
    RunEnvironmentBinding,
)

_LOCK_ADAPTER = TypeAdapter(EnvironmentProviderLock)
_BINDINGS_ADAPTER = TypeAdapter(tuple[EnvironmentCredentialBinding, ...])
_CONNECTION_ADAPTER = TypeAdapter(EnvironmentConnectionSpec)


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
            updated_at=assume_utc(self.updated_at),
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
            archived_at=assume_utc(self.archived_at) if self.archived_at is not None else None,
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class EnvironmentTargetRecord(Base):
    __tablename__ = "environment_targets"
    __table_args__ = (
        CheckConstraint("length(target_key) BETWEEN 1 AND 1024", name="target_key_bounded"),
        CheckConstraint(
            "length(target_identity_digest_sha256) = 64",
            name="target_identity_digest_sha256",
        ),
        CheckConstraint(
            "retention_behavior IN ('none', 'while_execution_active')",
            name="retention_behavior_valid",
        ),
        CheckConstraint("status IN ('active', 'idle', 'retired')", name="status_valid"),
        CheckConstraint("active_run_count >= 0", name="active_run_count_non_negative"),
        CheckConstraint(
            "((status = 'active' AND active_run_count > 0) OR "
            "(status IN ('idle', 'retired') AND active_run_count = 0))",
            name="status_matches_active_run_count",
        ),
        CheckConstraint("keeper_claim_generation >= 0", name="keeper_claim_generation_non_negative"),
        CheckConstraint("operation_generation >= 0", name="operation_generation_non_negative"),
        CheckConstraint(
            "((operation_id IS NULL AND requested_alive_until IS NULL) OR "
            "(operation_id IS NOT NULL AND requested_alive_until IS NOT NULL))",
            name="operation_fields_together",
        ),
        CheckConstraint(
            "((keeper_owner_worker_generation IS NULL AND keeper_lease_expires_at IS NULL "
            "AND keeper_source_binding_id IS NULL) OR "
            "(keeper_owner_worker_generation IS NOT NULL AND keeper_lease_expires_at IS NOT NULL "
            "AND keeper_source_binding_id IS NOT NULL))",
            name="keeper_claim_fields_together",
        ),
        UniqueConstraint(
            "provider_key",
            "identity_schema_version",
            "target_identity_digest_sha256",
            name="uq_environment_targets_identity",
        ),
        Index(
            "ix_environment_targets_keepalive_due",
            "status",
            "retention_behavior",
            "next_keepalive_at",
            "id",
        ),
        Index("ix_environment_targets_retire_due", "status", "retire_after", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(128), nullable=False)
    identity_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    target_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_identity_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_behavior: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    active_run_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retire_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    keeper_claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    keeper_owner_worker_generation: Mapped[str | None] = mapped_column(String(256))
    keeper_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    keeper_source_binding_id: Mapped[str | None] = mapped_column(String(72))
    operation_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(72))
    requested_alive_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_alive_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_keepalive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> EnvironmentTarget:
        return EnvironmentTarget(
            id=self.id,
            provider_key=self.provider_key,
            identity_schema_version=self.identity_schema_version,
            target_key=self.target_key,
            target_identity_digest_sha256=self.target_identity_digest_sha256,
            retention_behavior=EnvironmentTargetRetentionBehavior(self.retention_behavior),
            status=EnvironmentTargetStatus(self.status),
            active_run_count=self.active_run_count,
            idle_at=assume_utc(self.idle_at) if self.idle_at is not None else None,
            retire_after=assume_utc(self.retire_after) if self.retire_after is not None else None,
            keeper_claim_generation=self.keeper_claim_generation,
            keeper_owner_worker_generation=self.keeper_owner_worker_generation,
            keeper_lease_expires_at=(
                assume_utc(self.keeper_lease_expires_at) if self.keeper_lease_expires_at is not None else None
            ),
            keeper_source_binding_id=self.keeper_source_binding_id,
            operation_generation=self.operation_generation,
            operation_id=self.operation_id,
            requested_alive_until=(
                assume_utc(self.requested_alive_until) if self.requested_alive_until is not None else None
            ),
            acknowledged_alive_until=(
                assume_utc(self.acknowledged_alive_until) if self.acknowledged_alive_until is not None else None
            ),
            next_keepalive_at=(assume_utc(self.next_keepalive_at) if self.next_keepalive_at is not None else None),
            last_error=self.last_error,
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class EnvironmentRevisionRecord(Base):
    __tablename__ = "environment_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("environment_id", "organization_id", "workspace_id"),
            ("environments.id", "environments.organization_id", "environments.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(("environment_target_id",), ("environment_targets.id",), ondelete="RESTRICT"),
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
    connection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider_package_revision_id: Mapped[str | None] = mapped_column(String(72))
    provider_lock: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    credential_bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    access: Mapped[str] = mapped_column(String(16), nullable=False)
    environment_target_id: Mapped[str] = mapped_column(String(72), nullable=False)
    target_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    logical_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> EnvironmentRevision:
        return EnvironmentRevision(
            id=self.id,
            environment_id=self.environment_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            version=self.version,
            connection=_CONNECTION_ADAPTER.validate_python(self.connection),
            provider_package_revision_id=self.provider_package_revision_id,
            provider_lock=_LOCK_ADAPTER.validate_python(self.provider_lock),
            credential_bindings=_BINDINGS_ADAPTER.validate_python(self.credential_bindings),
            access=EnvironmentAccess(self.access),
            environment_target_id=self.environment_target_id,
            target_key=self.target_key,
            logical_digest_sha256=self.logical_digest_sha256,
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=assume_utc(self.created_at),
        )


class RunEnvironmentBindingRecord(Base):
    __tablename__ = "run_environment_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("organization_id", "run_id"),
            ("runs.tenant_id", "runs.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(("environment_target_id",), ("environment_targets.id",), ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ("source_environment_revision_id", "organization_id", "workspace_id"),
            (
                "environment_revisions.id",
                "environment_revisions.organization_id",
                "environment_revisions.workspace_id",
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint("mount_name = 'workspace'", name="mount_name_workspace"),
        CheckConstraint("length(target_key) BETWEEN 1 AND 1024", name="target_key_bounded"),
        CheckConstraint(
            "length(environment_execution_config_digest_sha256) = 64",
            name="execution_config_digest_sha256",
        ),
        UniqueConstraint("organization_id", "run_id", name="uq_run_environment_bindings_run"),
        Index(
            "ix_run_environment_bindings_target",
            "environment_target_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    mount_name: Mapped[str] = mapped_column(String(32), nullable=False)
    source_environment_revision_id: Mapped[str | None] = mapped_column(String(72))
    environment_target_id: Mapped[str] = mapped_column(String(72), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    environment_execution_config_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> RunEnvironmentBinding:
        return RunEnvironmentBinding(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            mount_name="workspace",
            source_environment_revision_id=self.source_environment_revision_id,
            environment_target_id=self.environment_target_id,
            provider_key=self.provider_key,
            target_key=self.target_key,
            environment_execution_config_digest_sha256=self.environment_execution_config_digest_sha256,
            created_at=assume_utc(self.created_at),
        )


def _principal(value: str, principal_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(value), principal_id=principal_id)
