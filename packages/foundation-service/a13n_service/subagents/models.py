"""Relational authority for asynchronous child Run relationships."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.temporal import assume_utc

from .domain import (
    ChildCancellationPolicy,
    ChildResultVisibility,
    ChildRunRelationship,
)


class ChildRunRelationshipRecord(Base):
    __tablename__ = "child_run_relationships"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "parent_run_id"),
            ("runs.tenant_id", "runs.id"),
            name="fk_child_run_relationships_parent_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "tenant_id",
                "parent_run_id",
                "parent_run_attempt_id",
                "parent_run_attempt_generation",
            ),
            (
                "run_attempts.tenant_id",
                "run_attempts.run_id",
                "run_attempts.id",
                "run_attempts.fence",
            ),
            name="fk_child_run_relationships_parent_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "child_thread_id"),
            ("threads.tenant_id", "threads.id"),
            name="fk_child_run_relationships_child_thread",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "child_thread_id", "child_run_id"),
            ("runs.tenant_id", "runs.thread_id", "runs.id"),
            name="fk_child_run_relationships_child_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint("parent_run_attempt_generation >= 1", name="parent_attempt_generation_positive"),
        CheckConstraint("length(subagent_name) BETWEEN 1 AND 63", name="subagent_name_bounded"),
        CheckConstraint(
            "cancellation_policy IN ('independent', 'request_child_cancel')",
            name="cancellation_policy_valid",
        ),
        CheckConstraint(
            "result_visibility IN ('parent_thread', 'session')",
            name="result_visibility_valid",
        ),
        CheckConstraint("parent_run_id <> child_run_id", name="child_run_distinct"),
        UniqueConstraint("tenant_id", "id", name="uq_child_run_relationships_tenant_id"),
        UniqueConstraint("tenant_id", "child_run_id", name="uq_child_run_relationships_child_run"),
        Index("ix_child_run_relationships_parent", "tenant_id", "parent_run_id", "created_at", "id"),
        Index("ix_child_run_relationships_child_thread", "tenant_id", "child_thread_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    parent_run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    parent_run_attempt_id: Mapped[str] = mapped_column(String(72), nullable=False)
    parent_run_attempt_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subagent_name: Mapped[str] = mapped_column(String(63), nullable=False)
    child_run_id: Mapped[str] = mapped_column(String(72), nullable=False)
    child_thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    cancellation_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    result_visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> ChildRunRelationship:
        return ChildRunRelationship(
            id=self.id,
            parent_run_id=self.parent_run_id,
            parent_run_attempt_id=self.parent_run_attempt_id,
            parent_run_attempt_generation=self.parent_run_attempt_generation,
            subagent_name=self.subagent_name,
            child_run_id=self.child_run_id,
            child_thread_id=self.child_thread_id,
            cancellation_policy=ChildCancellationPolicy(self.cancellation_policy),
            result_visibility=ChildResultVisibility(self.result_visibility),
            created_at=assume_utc(self.created_at),
        )


__all__ = ["ChildRunRelationshipRecord"]
