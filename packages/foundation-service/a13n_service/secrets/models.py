"""Relational managed Secret resource."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class SecretRecord(Base):
    __tablename__ = "secrets"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "owner_type IN ('workspace', 'user', 'ingress', 'connector_provider', 'mcp_connection', 'a2a_push_configuration')",
            name="owner_type_valid",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(deleted_at IS NULL AND ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL) "
            "OR (deleted_at IS NOT NULL AND ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL)",
            name="active_material_consistent",
        ),
        CheckConstraint("owner_type != 'workspace' OR owner_id = workspace_id", name="workspace_owner_consistent"),
        Index(
            "uq_secrets_active_owner_key",
            "organization_id",
            "workspace_id",
            "owner_type",
            "owner_id",
            "key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_secrets_owner_listing",
            "organization_id",
            "workspace_id",
            "owner_type",
            "owner_id",
            "key",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    owner_type: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str] = mapped_column(String(72))
    key: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(BigInteger)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
    encryption_key_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
