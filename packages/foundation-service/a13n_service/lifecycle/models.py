"""Relational storage for immutable lifecycle facts and projection bookkeeping."""

from __future__ import annotations

from datetime import datetime

from a13n_harness import SafeFailure
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.temporal import assume_utc, optional_assume_utc

from .domain import LifecycleEntityType, LifecycleEvent, LifecycleProjectionState

_FAILURE_ADAPTER = TypeAdapter(SafeFailure | None)


class LifecycleEventRecord(Base):
    __tablename__ = "lifecycle_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "session_id"),
            ("sessions.tenant_id", "sessions.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "run_id"),
            ("runs.tenant_id", "runs.id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "run_id", "run_attempt_id"),
            ("run_attempts.tenant_id", "run_attempts.run_id", "run_attempts.id"),
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", name="uq_lifecycle_events_id"),
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "entity_id",
            "resource_seq",
            name="uq_lifecycle_events_resource_seq",
        ),
        UniqueConstraint(
            "tenant_id",
            "mutation_id",
            "event_type",
            "entity_type",
            "entity_id",
            name="uq_lifecycle_events_mutation_fact",
        ),
        CheckConstraint("resource_seq >= 1", name="resource_seq_positive"),
        CheckConstraint("entity_version >= 1", name="entity_version_positive"),
        CheckConstraint("length(schema_version) >= 1", name="schema_version_non_blank"),
        CheckConstraint("projection_attempts >= 0", name="projection_attempts_non_negative"),
        CheckConstraint("entity_type IN ('run', 'run_attempt')", name="entity_type_valid"),
        CheckConstraint(
            "(entity_type = 'run' AND entity_id = run_id AND run_attempt_id IS NULL) OR "
            "(entity_type = 'run_attempt' AND entity_id = run_attempt_id AND run_attempt_id IS NOT NULL)",
            name="entity_correlation_valid",
        ),
        CheckConstraint(
            "event_type IN ('run.accepted', 'run.running', 'run.waiting', 'run.completed', 'run.failed', "
            "'run.cancelled', 'run_attempt.leased', 'run_attempt.running', 'run_attempt.succeeded', "
            "'run_attempt.yielded', 'run_attempt.failed', 'run_attempt.cancelled')",
            name="event_type_valid",
        ),
        CheckConstraint(
            "(projection_state = 'pending' AND projection_next_attempt_at IS NOT NULL "
            "AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL "
            "AND projected_at IS NULL) OR "
            "(projection_state = 'retry_wait' AND projection_next_attempt_at IS NOT NULL "
            "AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL "
            "AND projected_at IS NULL) OR "
            "(projection_state = 'projecting' AND projection_next_attempt_at IS NULL "
            "AND projection_lease_owner IS NOT NULL AND projection_lease_expires_at IS NOT NULL "
            "AND projected_at IS NULL) OR "
            "(projection_state = 'projected' AND projection_next_attempt_at IS NULL "
            "AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL "
            "AND projected_at IS NOT NULL) OR "
            "(projection_state = 'abandoned' AND projection_next_attempt_at IS NULL "
            "AND projection_lease_owner IS NULL AND projection_lease_expires_at IS NULL "
            "AND projected_at IS NULL)",
            name="projection_shape_valid",
        ),
        Index("ix_lifecycle_events_tenant_seq", "tenant_id", "seq"),
        Index("ix_lifecycle_events_resource", "tenant_id", "entity_type", "entity_id", "seq"),
        Index("ix_lifecycle_events_run", "tenant_id", "run_id", "seq"),
        Index("ix_lifecycle_events_attempt", "tenant_id", "run_attempt_id", "seq"),
        Index(
            "ix_lifecycle_events_projection_due",
            "projection_state",
            "projection_next_attempt_at",
            "seq",
            postgresql_where=text("projection_state IN ('pending', 'projecting', 'retry_wait')"),
            sqlite_where=text("projection_state IN ('pending', 'projecting', 'retry_wait')"),
        ),
    )

    seq: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    id: Mapped[str] = mapped_column(String(72), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(72), nullable=False)
    resource_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    mutation_id: Mapped[str] = mapped_column(String(72), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(72))
    thread_id: Mapped[str | None] = mapped_column(String(72))
    run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    run_attempt_id: Mapped[str | None] = mapped_column(String(72))
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(72))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    projection_state: Mapped[str] = mapped_column(String(32), nullable=False)
    projection_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection_lease_owner: Mapped[str | None] = mapped_column(String(128))
    projection_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection_error_json: Mapped[dict[str, object] | None] = mapped_column(JSON)

    def to_resource(self) -> LifecycleEvent:
        return LifecycleEvent(
            seq=self.seq,
            id=self.id,
            tenant_id=self.tenant_id,
            entity_type=LifecycleEntityType(self.entity_type),
            entity_id=self.entity_id,
            resource_seq=self.resource_seq,
            entity_version=self.entity_version,
            event_type=self.event_type,
            schema_version=self.schema_version,
            mutation_id=self.mutation_id,
            session_id=self.session_id,
            thread_id=self.thread_id,
            run_id=self.run_id,
            run_attempt_id=self.run_attempt_id,
            payload=self.payload,
            actor_type=self.actor_type,
            actor_id=self.actor_id,
            occurred_at=assume_utc(self.occurred_at),
            created_at=assume_utc(self.created_at),
            projection_state=LifecycleProjectionState(self.projection_state),
            projection_attempts=self.projection_attempts,
            projection_next_attempt_at=optional_assume_utc(self.projection_next_attempt_at),
            projection_lease_owner=self.projection_lease_owner,
            projection_lease_expires_at=optional_assume_utc(self.projection_lease_expires_at),
            projected_at=optional_assume_utc(self.projected_at),
            projection_error=_FAILURE_ADAPTER.validate_python(self.projection_error_json),
        )


__all__ = ["LifecycleEventRecord"]
