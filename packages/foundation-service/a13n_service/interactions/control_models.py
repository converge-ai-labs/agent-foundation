"""Relational authority for Thread inbox delivery and queued submissions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType

from .control_domain import (
    InboxPayloadObjectRef,
    QueuedSubmission,
    QueuedSubmissionFailure,
    QueuedSubmissionState,
    ThreadInboxCounter,
    ThreadInboxEntry,
    ThreadInboxKind,
    ThreadInboxStatus,
    ThreadRunSubmissionIntent,
)

_SUBMISSION_ADAPTER = TypeAdapter(ThreadRunSubmissionIntent)
_QUEUED_FAILURE_ADAPTER = TypeAdapter(QueuedSubmissionFailure)


def _run_references() -> tuple[ForeignKeyConstraint, ...]:
    return tuple(
        ForeignKeyConstraint(
            ("tenant_id", "thread_id", column),
            ("runs.tenant_id", "runs.thread_id", "runs.id"),
            name=f"fk_thread_inbox_{column}",
            ondelete="RESTRICT",
        )
        for column in (
            "accepted_against_run_id",
            "target_run_id",
            "source_waiting_run_id",
            "origin_run_id",
            "consumed_by_run_id",
        )
    )


class ThreadInboxCounterRecord(Base):
    __tablename__ = "thread_inbox_counters"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("next_delivery_sequence >= 1", name="next_delivery_sequence_positive"),
        CheckConstraint("pending_count >= 0", name="pending_count_non_negative"),
        CheckConstraint("pending_bytes >= 0", name="pending_bytes_non_negative"),
        UniqueConstraint("tenant_id", "thread_id", name="uq_thread_inbox_counters_scope"),
    )

    thread_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    next_delivery_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pending_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pending_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def to_resource(self) -> ThreadInboxCounter:
        return ThreadInboxCounter(
            tenant_id=self.tenant_id,
            thread_id=self.thread_id,
            next_delivery_sequence=self.next_delivery_sequence,
            pending_count=self.pending_count,
            pending_bytes=self.pending_bytes,
        )


class ThreadInboxRecord(Base):
    __tablename__ = "thread_inbox"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="CASCADE",
        ),
        *_run_references(),
        CheckConstraint("kind IN ('steer', 'async_subagent_result')", name="kind_valid"),
        CheckConstraint(
            "status IN ('pending', 'consumed', 'superseded', 'suppressed', 'expired', 'discarded')",
            name="status_valid",
        ),
        CheckConstraint("delivery_sequence >= 1", name="delivery_sequence_positive"),
        CheckConstraint(
            "(payload_object_key IS NULL AND payload_object_digest_sha256 IS NULL "
            "AND payload_object_size_bytes IS NULL AND payload_object_content_type IS NULL "
            "AND payload_object_schema_version IS NULL) OR "
            "(payload_object_key IS NOT NULL AND payload_object_digest_sha256 IS NOT NULL "
            "AND payload_object_size_bytes > 0 AND payload_object_content_type IS NOT NULL "
            "AND payload_object_schema_version IS NOT NULL)",
            name="payload_object_group_valid",
        ),
        CheckConstraint("(payload_json IS NOT NULL) <> (payload_object_key IS NOT NULL)", name="payload_valid"),
        CheckConstraint(
            "(kind = 'steer' AND accepted_against_run_id IS NOT NULL AND origin_run_id IS NULL "
            "AND expires_at IS NULL AND status IN ('pending', 'consumed', 'superseded')) OR "
            "(kind = 'async_subagent_result' AND accepted_against_run_id IS NULL AND origin_run_id IS NOT NULL)",
            name="kind_provenance_valid",
        ),
        CheckConstraint(
            "(status = 'pending' AND finalized_at IS NULL AND consumed_by_run_id IS NULL "
            "AND consumed_state_digest_sha256 IS NULL AND consumed_checkpoint_seq IS NULL) OR "
            "(status = 'consumed' AND finalized_at IS NOT NULL AND target_run_id IS NOT NULL "
            "AND consumed_by_run_id = target_run_id AND consumed_state_digest_sha256 IS NOT NULL "
            "AND consumed_checkpoint_seq >= 0) OR "
            "(status IN ('superseded', 'suppressed', 'expired', 'discarded') AND finalized_at IS NOT NULL "
            "AND target_run_id IS NULL AND consumed_by_run_id IS NULL "
            "AND consumed_state_digest_sha256 IS NULL AND consumed_checkpoint_seq IS NULL)",
            name="status_evidence_valid",
        ),
        CheckConstraint(
            "status <> 'pending' OR ((target_run_id IS NOT NULL AND source_waiting_run_id IS NULL) "
            "OR (target_run_id IS NULL AND source_waiting_run_id IS NOT NULL) "
            "OR (target_run_id IS NOT NULL AND source_waiting_run_id IS NOT NULL "
            "AND target_run_id <> source_waiting_run_id) "
            "OR (kind = 'async_subagent_result' AND target_run_id IS NULL AND source_waiting_run_id IS NULL))",
            name="pending_binding_valid",
        ),
        CheckConstraint(
            "target_run_id IS NULL OR source_waiting_run_id IS NULL OR target_run_id <> source_waiting_run_id",
            name="target_waiting_source_distinct",
        ),
        CheckConstraint(
            "consumed_state_digest_sha256 IS NULL OR length(consumed_state_digest_sha256) = 64",
            name="consumed_digest_sha256",
        ),
        CheckConstraint(
            "payload_object_digest_sha256 IS NULL OR length(payload_object_digest_sha256) = 64",
            name="payload_digest_sha256",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_thread_inbox_tenant_id"),
        UniqueConstraint("tenant_id", "thread_id", "delivery_sequence", name="uq_thread_inbox_sequence"),
        Index("ix_thread_inbox_fifo", "tenant_id", "thread_id", "status", "delivery_sequence"),
        Index("ix_thread_inbox_target", "tenant_id", "target_run_id", "status", "delivery_sequence"),
        Index(
            "ix_thread_inbox_waiting_source",
            "tenant_id",
            "source_waiting_run_id",
            "status",
            "delivery_sequence",
        ),
        Index("ix_thread_inbox_kind_scan", "tenant_id", "kind", "status", "delivery_sequence"),
        Index("ix_thread_inbox_origin", "tenant_id", "origin_run_id", "kind", "status", "delivery_sequence"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_against_run_id: Mapped[str | None] = mapped_column(String(72))
    target_run_id: Mapped[str | None] = mapped_column(String(72))
    source_waiting_run_id: Mapped[str | None] = mapped_column(String(72))
    origin_run_id: Mapped[str | None] = mapped_column(String(72))
    payload_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))
    payload_object_key: Mapped[str | None] = mapped_column(String(1024))
    payload_object_digest_sha256: Mapped[str | None] = mapped_column(String(64))
    payload_object_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    payload_object_content_type: Mapped[str | None] = mapped_column(String(255))
    payload_object_schema_version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    consumed_by_run_id: Mapped[str | None] = mapped_column(String(72))
    consumed_state_digest_sha256: Mapped[str | None] = mapped_column(String(64))
    consumed_checkpoint_seq: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self) -> ThreadInboxEntry:
        payload: dict[str, Any] = {}
        if self.payload_object_key is None:
            payload["payload"] = _json_null(self.payload_json)
        else:
            payload["payload_object"] = InboxPayloadObjectRef(
                object_key=self.payload_object_key,
                digest_sha256=_required(self.payload_object_digest_sha256),
                size_bytes=_required(self.payload_object_size_bytes),
                content_type=_required(self.payload_object_content_type),
                schema_version=_required(self.payload_object_schema_version),
            )
        return ThreadInboxEntry(
            id=self.id,
            tenant_id=self.tenant_id,
            thread_id=self.thread_id,
            kind=ThreadInboxKind(self.kind),
            delivery_sequence=self.delivery_sequence,
            accepted_against_run_id=self.accepted_against_run_id,
            target_run_id=self.target_run_id,
            source_waiting_run_id=self.source_waiting_run_id,
            origin_run_id=self.origin_run_id,
            payload_schema_version=self.payload_schema_version,
            status=ThreadInboxStatus(self.status),
            consumed_by_run_id=self.consumed_by_run_id,
            consumed_state_digest_sha256=self.consumed_state_digest_sha256,
            consumed_checkpoint_seq=self.consumed_checkpoint_seq,
            expires_at=_optional_utc(self.expires_at),
            created_at=_as_utc(self.created_at),
            finalized_at=_optional_utc(self.finalized_at),
            **payload,
        )


class QueuedSubmissionRecord(Base):
    __tablename__ = "thread_queued_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "thread_id"),
            ("threads.tenant_id", "threads.id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            (
                "tenant_id",
                "thread_id",
                "consumed_run_id",
                "authority_principal_type",
                "authority_principal_id",
            ),
            (
                "runs.tenant_id",
                "runs.thread_id",
                "runs.id",
                "runs.authority_principal_type",
                "runs.authority_principal_id",
            ),
            name="fk_queued_submissions_consumed_run_authority",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("authority_principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        CheckConstraint("length(submission_digest_sha256) = 64", name="submission_digest_sha256"),
        CheckConstraint(
            "(position IS NOT NULL AND position >= 1 "
            "AND consumed_run_id IS NULL AND consumed_at IS NULL AND failure_json IS NULL AND failed_at IS NULL) OR "
            "(position IS NULL AND consumed_run_id IS NOT NULL AND consumed_at IS NOT NULL "
            "AND failure_json IS NULL AND failed_at IS NULL) OR "
            "(position IS NULL AND consumed_run_id IS NULL AND consumed_at IS NULL "
            "AND failure_json IS NOT NULL AND failed_at IS NOT NULL)",
            name="lifecycle_valid",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_thread_queued_submissions_tenant_id"),
        UniqueConstraint("tenant_id", "consumed_run_id", name="uq_thread_queued_submissions_consumed_run"),
        Index(
            "uq_thread_queued_submissions_position",
            "tenant_id",
            "thread_id",
            "position",
            unique=True,
            postgresql_where=text("position IS NOT NULL"),
            sqlite_where=text("position IS NOT NULL"),
        ),
        Index(
            "ix_thread_queued_submissions_live",
            "tenant_id",
            "thread_id",
            "position",
            "id",
            postgresql_where=text("position IS NOT NULL"),
            sqlite_where=text("position IS NOT NULL"),
        ),
        Index(
            "ix_thread_queued_submissions_consumed",
            "tenant_id",
            "thread_id",
            "consumed_at",
            "id",
        ),
        Index(
            "ix_thread_queued_submissions_failed",
            "tenant_id",
            "thread_id",
            "failed_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(72), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(72), nullable=False)
    authority_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    position: Mapped[int | None] = mapped_column(BigInteger)
    submission_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    submission_digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_run_id: Mapped[str | None] = mapped_column(String(72))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> QueuedSubmission:
        consumed = self.consumed_run_id is not None
        failed = self.failure_json is not None
        return QueuedSubmission(
            queued_submission_id=self.id,
            version=self.version,
            thread_id=self.thread_id,
            authority_principal=PrincipalRef(
                principal_type=PrincipalType(self.authority_principal_type),
                principal_id=self.authority_principal_id,
            ),
            position=self.position,
            submission=_SUBMISSION_ADAPTER.validate_python(self.submission_json),
            submission_digest_sha256=self.submission_digest_sha256,
            state=(
                QueuedSubmissionState.consumed
                if consumed
                else QueuedSubmissionState.failed
                if failed
                else QueuedSubmissionState.queued
            ),
            consumed_run_id=self.consumed_run_id,
            failure=None if self.failure_json is None else _QUEUED_FAILURE_ADAPTER.validate_python(self.failure_json),
            created_at=_as_utc(self.created_at),
            updated_at=_as_utc(self.updated_at),
            consumed_at=_optional_utc(self.consumed_at),
            failed_at=_optional_utc(self.failed_at),
        )


def _json_null(value: Any) -> Any:
    return None if value is JSON.NULL else value


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("relational payload reference is incomplete")
    return value


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)


__all__ = ["QueuedSubmissionRecord", "ThreadInboxCounterRecord", "ThreadInboxRecord"]
