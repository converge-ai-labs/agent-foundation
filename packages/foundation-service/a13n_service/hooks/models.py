"""Relational authority for HookSubscription heads and immutable Revisions."""

from __future__ import annotations

from datetime import UTC, datetime

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType

from .domain import CreateHookSubscriptionRequest, HookSubscription, HookSubscriptionRevision, WebhookDestinationConfig

_HOOK_NAMES_TYPE = JSON().with_variant(JSONB(), "postgresql")


class HookSubscriptionRecord(Base):
    __tablename__ = "hook_subscriptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("current_revision_id", "id", "organization_id", "workspace_id"),
            (
                "hook_subscription_revisions.id",
                "hook_subscription_revisions.hook_subscription_id",
                "hook_subscription_revisions.organization_id",
                "hook_subscription_revisions.workspace_id",
            ),
            name="fk_hook_subscriptions_current_revision",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint("updated_by_type IN ('user', 'service_account')", name="updated_by_type_valid"),
        UniqueConstraint("id", "organization_id", "workspace_id", name="uq_hook_subscriptions_tenant_id"),
        UniqueConstraint("organization_id", "inline_run_id", name="uq_hook_subscriptions_inline_run"),
        Index(
            "ix_hook_subscriptions_active_workspace",
            "organization_id",
            "workspace_id",
            "id",
            postgresql_where=text("enabled AND deleted_at IS NULL"),
            sqlite_where=text("enabled = 1 AND deleted_at IS NULL"),
        ),
        Index("ix_hook_subscriptions_workspace_updated", "organization_id", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_revision_id: Mapped[str] = mapped_column(String(72), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inline_run_id: Mapped[str | None] = mapped_column(String(72))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    updated_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self, revision: HookSubscriptionRevisionRecord) -> HookSubscription:
        return HookSubscription(
            id=self.id,
            version=self.version,
            current_revision_id=self.current_revision_id,
            workspace_id=self.workspace_id,
            enabled=self.enabled,
            deleted_at=_optional_utc(self.deleted_at),
            created_by=_principal(self.created_by_type, self.created_by_id),
            updated_by=_principal(self.updated_by_type, self.updated_by_id),
            created_at=_utc(self.created_at),
            updated_at=_utc(self.updated_at),
            current_revision=revision.to_resource(),
        )


class HookSubscriptionRevisionRecord(Base):
    __tablename__ = "hook_subscription_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("hook_subscription_id", "organization_id", "workspace_id"),
            ("hook_subscriptions.id", "hook_subscriptions.organization_id", "hook_subscriptions.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "session_id"),
            ("sessions.tenant_id", "sessions.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "run_id"),
            ("runs.tenant_id", "runs.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("signature_profile = 'hmac_sha256_v1'", name="signature_profile_valid"),
        CheckConstraint("length(endpoint_url) BETWEEN 1 AND 8192", name="endpoint_url_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        UniqueConstraint("hook_subscription_id", "version", name="uq_hook_subscription_revisions_number"),
        UniqueConstraint(
            "id",
            "hook_subscription_id",
            "organization_id",
            "workspace_id",
            name="uq_hook_subscription_revisions_head_authority",
        ),
        Index("uq_hook_subscription_revisions_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("ix_hook_subscription_revisions_hook_names", "hook_names", postgresql_using="gin"),
        Index(
            "ix_hook_subscription_revisions_session",
            "organization_id",
            "session_id",
            "id",
            postgresql_where=text("session_id IS NOT NULL"),
            sqlite_where=text("session_id IS NOT NULL"),
        ),
        Index(
            "ix_hook_subscription_revisions_thread",
            "organization_id",
            "thread_id",
            "id",
            postgresql_where=text("thread_id IS NOT NULL"),
            sqlite_where=text("thread_id IS NOT NULL"),
        ),
        Index(
            "ix_hook_subscription_revisions_run",
            "organization_id",
            "run_id",
            "id",
            postgresql_where=text("run_id IS NOT NULL"),
            sqlite_where=text("run_id IS NOT NULL"),
        ),
        Index("ix_hook_subscription_revisions_head", "hook_subscription_id", "version", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    hook_subscription_id: Mapped[str] = mapped_column(String(72), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hook_names: Mapped[list[str]] = mapped_column(_HOOK_NAMES_TYPE, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(72))
    thread_id: Mapped[str | None] = mapped_column(String(72))
    run_id: Mapped[str | None] = mapped_column(String(72))
    endpoint_url: Mapped[str] = mapped_column(String(8192), nullable=False)
    signing_secret_id: Mapped[str] = mapped_column(
        String(72),
        ForeignKey("secrets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    signature_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> HookSubscriptionRevision:
        signature_profile = self.signature_profile
        if signature_profile != "hmac_sha256_v1":
            raise ValueError("persisted Hook signature profile is invalid")
        return HookSubscriptionRevision(
            id=self.id,
            hook_subscription_id=self.hook_subscription_id,
            version=self.version,
            hook_names=tuple(self.hook_names),
            session_id=self.session_id,
            thread_id=self.thread_id,
            run_id=self.run_id,
            webhook=WebhookDestinationConfig(
                endpoint_url=self.endpoint_url,
                signing_secret_id=self.signing_secret_id,
                signature_profile=signature_profile,
            ),
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=_utc(self.created_at),
        )

    def configuration(self) -> CreateHookSubscriptionRequest:
        signature_profile = self.signature_profile
        if signature_profile != "hmac_sha256_v1":
            raise ValueError("persisted Hook signature profile is invalid")
        return CreateHookSubscriptionRequest(
            hook_names=tuple(self.hook_names),
            session_id=self.session_id,
            thread_id=self.thread_id,
            run_id=self.run_id,
            webhook=WebhookDestinationConfig(
                endpoint_url=self.endpoint_url,
                signing_secret_id=self.signing_secret_id,
                signature_profile=signature_profile,
            ),
        )


def _principal(principal_type: str, principal_id: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(principal_type), principal_id=principal_id)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _utc(value)


__all__ = ["HookSubscriptionRecord", "HookSubscriptionRevisionRecord"]
