"""Account-owned external identity and encrypted credentials."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.temporal import assume_utc

from .domain import Account, AccountStatus


class AccountRecord(ResourceCredential[str], Base):
    credential_owner_type = "application_account"
    __tablename__ = "application_accounts"
    __table_args__ = (
        CheckConstraint(
            "(deleted_at IS NULL AND ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL) OR (deleted_at IS NOT NULL AND ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL)",
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
        Index(
            "uq_application_accounts_identity",
            "workspace_id",
            "provider_key",
            "identity_digest",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("uq_application_accounts_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index(
            "uq_application_accounts_workspace_name",
            "workspace_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_application_accounts_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_application_accounts_provider_status", "provider_key", "status", "id"),
    )

    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> Account:
        return Account(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=self.name,
            provider_key=self.provider_key,
            provider_config_version=self.provider_config_version,
            provider_config=self.provider_config_json,
            status=AccountStatus(self.status),
            version=self.version,
            credential_configured=self.ciphertext is not None,
            credential_generation=self.credential_generation,
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )
