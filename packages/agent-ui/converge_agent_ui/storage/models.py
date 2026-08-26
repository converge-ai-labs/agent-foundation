"""Foundation tables for Agent UI local-store ownership and object registration."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .metadata import Base


class StoreLeaseRecord(Base):
    """The process generation currently owning this data root."""

    __tablename__ = "store_lease"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    process_generation: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ImmutableObjectRecord(Base):
    """One verified immutable object known to the metadata store."""

    __tablename__ = "immutable_object"

    logical_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RecoveryDiagnosticRecord(Base):
    """Bounded evidence from local-store recovery and quarantine."""

    __tablename__ = "recovery_diagnostic"

    diagnostic_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    process_generation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ImmutableObjectRecord", "RecoveryDiagnosticRecord", "StoreLeaseRecord"]
