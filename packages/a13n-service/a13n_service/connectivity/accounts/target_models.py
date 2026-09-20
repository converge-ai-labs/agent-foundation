"""One configuration per Account and exact provider object."""

from datetime import datetime
from typing import Any

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
from a13n_service.durable_operations.models import EntityRequestKey
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.temporal import assume_utc

from .reception import InputBatchingPolicy, InputOverride
from .targets import AccountTarget


class AccountTargetRecord(EntityRequestKey, Base):
    __tablename__ = "account_targets"
    __table_args__ = (
        ForeignKeyConstraint(
            ("account_id", "organization_id", "workspace_id"),
            ("application_accounts.id", "application_accounts.organization_id", "application_accounts.workspace_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("agent_id", "organization_id", "workspace_id"),
            ("agents.id", "agents.organization_id", "agents.workspace_id"),
            ondelete="RESTRICT",
        ),
        UniqueConstraint("account_id", "target_kind", "external_target_id", name="uq_account_targets_object"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("target_kind IN ('conversation', 'repository')", name="target_kind_valid"),
        Index("uq_account_targets_id_organization", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_account_targets_listing", "account_id", "updated_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    account_id: Mapped[str] = mapped_column(String(72), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    external_target_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(72))
    config_override_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    input_batching_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    provider_policy_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    receive_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> AccountTarget:
        return AccountTarget.model_validate(
            dict(
                id=self.id,
                organization_id=self.organization_id,
                workspace_id=self.workspace_id,
                account_id=self.account_id,
                target_kind=self.target_kind,
                external_target_id=self.external_target_id,
                agent_id=self.agent_id,
                config_override=InputOverride.model_validate(self.config_override_json)
                if self.config_override_json is not None
                else None,
                input_batching=InputBatchingPolicy.model_validate(self.input_batching_json)
                if self.input_batching_json is not None
                else None,
                provider_policy=self.provider_policy_json,
                receive_enabled=self.receive_enabled,
                version=self.version,
                created_by=PrincipalRef(
                    principal_type=PrincipalType(self.created_by_type), principal_id=self.created_by_id
                ),
                created_at=assume_utc(self.created_at),
                updated_at=assume_utc(self.updated_at),
            )
        )
