"""Service-owned navigation and access records; document bodies stay in Providers."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.temporal import assume_utc

from .domain import DocumentEntry, Scope, ScopeSettings


class ScopeRecord(Base):
    __tablename__ = "bot_memory_scopes"
    __table_args__ = (
        ForeignKeyConstraint(
            ("account_id", "organization_id", "workspace_id"),
            ("application_accounts.id", "application_accounts.organization_id", "application_accounts.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("provider_id", "organization_id"),
            ("memory_providers.id", "memory_providers.organization_id"),
            ondelete="RESTRICT",
        ),
        UniqueConstraint("account_id", "provider_id", "external_conversation_id", name="uq_bot_memory_scope_identity"),
        CheckConstraint("audience IN ('public', 'private', 'direct', 'unknown')", name="audience_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_bot_memory_scopes_account", "account_id", "provider_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    account_id: Mapped[str] = mapped_column(String(72))
    provider_id: Mapped[str] = mapped_column(String(72))
    external_conversation_id: Mapped[str] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(256))
    audience: Mapped[str] = mapped_column(String(16), default="unknown")
    settings_json: Mapped[dict[str, object]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    binding_floor: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    sharing_initialized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    def to_resource(self) -> Scope:
        return Scope.model_validate(
            {
                **ScopeSettings.model_validate(self.settings_json).model_dump(),
                "id": self.id,
                "account_id": self.account_id,
                "provider_id": self.provider_id,
                "external_conversation_id": self.external_conversation_id,
                "name": self.name,
                "audience": self.audience,
                "version": self.version,
            }
        )


class DocumentRecord(Base):
    __tablename__ = "bot_memory_documents"
    __table_args__ = (
        UniqueConstraint("scope_id", "request_key", name="uq_bot_memory_document_request"),
        UniqueConstraint("scope_id", "native_id", name="uq_bot_memory_document_native"),
        CheckConstraint("state IN ('pending', 'active', 'unconfirmed', 'deleting', 'deleted')", name="state_valid"),
        CheckConstraint("kind IN ('semantic', 'procedural', 'episodic', 'daily', 'long_term')", name="kind_valid"),
        CheckConstraint(
            "state != 'active' OR (native_id IS NOT NULL AND saved_at IS NOT NULL)", name="active_confirmed"
        ),
        Index("ix_bot_memory_documents_browse", "scope_id", "state", "activity_date", "id"),
        Index("ix_bot_memory_documents_source", "publication_source_id", "state"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(72), ForeignKey("bot_memory_scopes.id", ondelete="RESTRICT"))
    native_id: Mapped[str | None] = mapped_column(String(512))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    request_key: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    body_digest: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(320))
    kind: Mapped[str] = mapped_column(String(16))
    activity_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON)
    correction_of: Mapped[str | None] = mapped_column(
        String(72), ForeignKey("bot_memory_documents.id", ondelete="RESTRICT")
    )
    publication_source_id: Mapped[str | None] = mapped_column(
        String(72), ForeignKey("bot_memory_documents.id", ondelete="RESTRICT")
    )
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)

    def to_entry(self, *, shared: bool = False, expose_source: bool = True) -> DocumentEntry:
        return DocumentEntry.model_validate(
            {
                "id": self.id,
                "scope_id": self.scope_id,
                "path": f"{self.id}.md",
                "title": self.title,
                "description": self.description,
                "kind": None if self.kind in {"daily", "long_term"} else self.kind,
                "legacy_kind": self.kind if self.kind in {"daily", "long_term"} else None,
                "activity_date": self.activity_date,
                "timezone": self.timezone,
                "saved_at": assume_utc(self.saved_at) if self.saved_at else None,
                "state": self.state,
                "correction_of": self.correction_of if expose_source else None,
                "publication_source_id": self.publication_source_id if expose_source else None,
                "shared": shared,
                "version": self.version,
            }
        )


# Retained legacy tables preserve deployed data and audit history. No access predicate
# or write path uses their grants; source deletion still cleans up old Provider copies.
class PublicationRecipientRecord(Base):
    __tablename__ = "bot_memory_publication_recipients"
    document_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("bot_memory_documents.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("bot_memory_scopes.id", ondelete="RESTRICT"), primary_key=True
    )


class SharingPolicyRecord(Base):
    __tablename__ = "bot_memory_sharing_policies"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_bot_memory_policies_account", "account_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(72), ForeignKey("application_accounts.id", ondelete="RESTRICT"))
    provider_id: Mapped[str] = mapped_column(String(72), ForeignKey("memory_providers.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(128))
    kinds_json: Mapped[list[str]] = mapped_column(JSON)
    include_history: Mapped[bool] = mapped_column(Boolean, default=False)
    enroll_future_groups: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    future_since: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SharingParticipantRecord(Base):
    __tablename__ = "bot_memory_sharing_participants"
    policy_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("bot_memory_sharing_policies.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("bot_memory_scopes.id", ondelete="RESTRICT"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
