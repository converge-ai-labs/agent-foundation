"""initialize interaction control queues.

Revision ID: 69a28e8783ad
Revises: 7de20ce04aa6
Create Date: 2026-09-04 08:21:40.283725+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "69a28e8783ad"
down_revision: str | Sequence[str] | None = "7de20ce04aa6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "thread_inbox_counters",
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("next_delivery_sequence", sa.BigInteger(), nullable=False),
        sa.Column("pending_count", sa.BigInteger(), nullable=False),
        sa.Column("pending_bytes", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "next_delivery_sequence >= 1", name=op.f("ck_thread_inbox_counters_next_delivery_sequence_positive")
        ),
        sa.CheckConstraint("pending_bytes >= 0", name=op.f("ck_thread_inbox_counters_pending_bytes_non_negative")),
        sa.CheckConstraint("pending_count >= 0", name=op.f("ck_thread_inbox_counters_pending_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["threads.organization_id", "threads.id"],
            name=op.f("fk_thread_inbox_counters_organization_id_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("thread_id", name=op.f("pk_thread_inbox_counters")),
        sa.UniqueConstraint("organization_id", "thread_id", name="uq_thread_inbox_counters_scope"),
    )
    op.create_table(
        "thread_inbox",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("delivery_sequence", sa.BigInteger(), nullable=False),
        sa.Column("accepted_against_run_id", sa.String(length=72), nullable=True),
        sa.Column("target_run_id", sa.String(length=72), nullable=True),
        sa.Column("source_waiting_run_id", sa.String(length=72), nullable=True),
        sa.Column("origin_run_id", sa.String(length=72), nullable=True),
        sa.Column("payload_schema_version", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("payload_object_key", sa.String(length=1024), nullable=True),
        sa.Column("payload_object_digest_sha256", sa.String(length=64), nullable=True),
        sa.Column("payload_object_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("payload_object_content_type", sa.String(length=255), nullable=True),
        sa.Column("payload_object_schema_version", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("consumed_by_run_id", sa.String(length=72), nullable=True),
        sa.Column("consumed_state_digest_sha256", sa.String(length=64), nullable=True),
        sa.Column("consumed_checkpoint_seq", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(kind = 'steer' AND accepted_against_run_id IS NOT NULL AND origin_run_id IS NULL AND expires_at IS NULL AND status IN ('pending', 'consumed', 'superseded')) OR (kind = 'async_subagent_result' AND accepted_against_run_id IS NULL AND origin_run_id IS NOT NULL)",
            name=op.f("ck_thread_inbox_kind_provenance_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND finalized_at IS NULL AND consumed_by_run_id IS NULL AND consumed_state_digest_sha256 IS NULL AND consumed_checkpoint_seq IS NULL) OR (status = 'consumed' AND finalized_at IS NOT NULL AND target_run_id IS NOT NULL AND consumed_by_run_id = target_run_id AND consumed_state_digest_sha256 IS NOT NULL AND consumed_checkpoint_seq >= 0) OR (status IN ('superseded', 'suppressed', 'expired', 'discarded') AND finalized_at IS NOT NULL AND target_run_id IS NULL AND consumed_by_run_id IS NULL AND consumed_state_digest_sha256 IS NULL AND consumed_checkpoint_seq IS NULL)",
            name=op.f("ck_thread_inbox_status_evidence_valid"),
        ),
        sa.CheckConstraint("kind IN ('steer', 'async_subagent_result')", name=op.f("ck_thread_inbox_kind_valid")),
        sa.CheckConstraint(
            "status <> 'pending' OR ((target_run_id IS NOT NULL AND source_waiting_run_id IS NULL) OR (target_run_id IS NULL AND source_waiting_run_id IS NOT NULL) OR (target_run_id IS NOT NULL AND source_waiting_run_id IS NOT NULL AND target_run_id <> source_waiting_run_id) OR (kind = 'async_subagent_result' AND target_run_id IS NULL AND source_waiting_run_id IS NULL))",
            name=op.f("ck_thread_inbox_pending_binding_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'consumed', 'superseded', 'suppressed', 'expired', 'discarded')",
            name=op.f("ck_thread_inbox_status_valid"),
        ),
        sa.CheckConstraint(
            "(payload_json IS NOT NULL) <> (payload_object_key IS NOT NULL)", name=op.f("ck_thread_inbox_payload_valid")
        ),
        sa.CheckConstraint(
            "(payload_object_key IS NULL AND payload_object_digest_sha256 IS NULL AND payload_object_size_bytes IS NULL AND payload_object_content_type IS NULL AND payload_object_schema_version IS NULL) OR (payload_object_key IS NOT NULL AND payload_object_digest_sha256 IS NOT NULL AND payload_object_size_bytes > 0 AND payload_object_content_type IS NOT NULL AND payload_object_schema_version IS NOT NULL)",
            name=op.f("ck_thread_inbox_payload_object_group_valid"),
        ),
        sa.CheckConstraint(
            "consumed_state_digest_sha256 IS NULL OR length(consumed_state_digest_sha256) = 64",
            name=op.f("ck_thread_inbox_consumed_digest_sha256"),
        ),
        sa.CheckConstraint("delivery_sequence >= 1", name=op.f("ck_thread_inbox_delivery_sequence_positive")),
        sa.CheckConstraint(
            "payload_object_digest_sha256 IS NULL OR length(payload_object_digest_sha256) = 64",
            name=op.f("ck_thread_inbox_payload_digest_sha256"),
        ),
        sa.CheckConstraint(
            "target_run_id IS NULL OR source_waiting_run_id IS NULL OR target_run_id <> source_waiting_run_id",
            name=op.f("ck_thread_inbox_target_waiting_source_distinct"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id", "accepted_against_run_id"],
            ["runs.organization_id", "runs.thread_id", "runs.id"],
            name="fk_thread_inbox_accepted_against_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id", "consumed_by_run_id"],
            ["runs.organization_id", "runs.thread_id", "runs.id"],
            name="fk_thread_inbox_consumed_by_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id", "origin_run_id"],
            ["runs.organization_id", "runs.thread_id", "runs.id"],
            name="fk_thread_inbox_origin_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id", "source_waiting_run_id"],
            ["runs.organization_id", "runs.thread_id", "runs.id"],
            name="fk_thread_inbox_source_waiting_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id", "target_run_id"],
            ["runs.organization_id", "runs.thread_id", "runs.id"],
            name="fk_thread_inbox_target_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["threads.organization_id", "threads.id"],
            name=op.f("fk_thread_inbox_organization_id_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_thread_inbox")),
        sa.UniqueConstraint("organization_id", "id", name="uq_thread_inbox_organization_id"),
        sa.UniqueConstraint("organization_id", "thread_id", "delivery_sequence", name="uq_thread_inbox_sequence"),
    )
    op.create_index(
        "ix_thread_inbox_fifo",
        "thread_inbox",
        ["organization_id", "thread_id", "status", "delivery_sequence"],
        unique=False,
    )
    op.create_index(
        "ix_thread_inbox_kind_scan",
        "thread_inbox",
        ["organization_id", "kind", "status", "delivery_sequence"],
        unique=False,
    )
    op.create_index(
        "ix_thread_inbox_origin",
        "thread_inbox",
        ["organization_id", "origin_run_id", "kind", "status", "delivery_sequence"],
        unique=False,
    )
    op.create_index(
        "ix_thread_inbox_target",
        "thread_inbox",
        ["organization_id", "target_run_id", "status", "delivery_sequence"],
        unique=False,
    )
    op.create_index(
        "ix_thread_inbox_waiting_source",
        "thread_inbox",
        ["organization_id", "source_waiting_run_id", "status", "delivery_sequence"],
        unique=False,
    )
    op.create_table(
        "thread_queued_submissions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("authority_principal_type", sa.String(length=32), nullable=False),
        sa.Column("authority_principal_id", sa.String(length=72), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=True),
        sa.Column("submission_json", sa.JSON(), nullable=False),
        sa.Column("submission_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("consumed_run_id", sa.String(length=72), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "authority_principal_type IN ('user', 'service_account')",
            name=op.f("ck_thread_queued_submissions_principal_type_valid"),
        ),
        sa.CheckConstraint(
            "(position IS NOT NULL AND position >= 1 AND consumed_run_id IS NULL AND consumed_at IS NULL AND failure_json IS NULL AND failed_at IS NULL) OR (position IS NULL AND consumed_run_id IS NOT NULL AND consumed_at IS NOT NULL AND failure_json IS NULL AND failed_at IS NULL) OR (position IS NULL AND consumed_run_id IS NULL AND consumed_at IS NULL AND failure_json IS NOT NULL AND failed_at IS NOT NULL)",
            name=op.f("ck_thread_queued_submissions_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "length(submission_digest_sha256) = 64", name=op.f("ck_thread_queued_submissions_submission_digest_sha256")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_thread_queued_submissions_version_positive")),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id", "consumed_run_id", "authority_principal_type", "authority_principal_id"],
            [
                "runs.organization_id",
                "runs.thread_id",
                "runs.id",
                "runs.authority_principal_type",
                "runs.authority_principal_id",
            ],
            name="fk_queued_submissions_consumed_run_authority",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["threads.organization_id", "threads.id"],
            name=op.f("fk_thread_queued_submissions_organization_id_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_thread_queued_submissions")),
        sa.UniqueConstraint("organization_id", "consumed_run_id", name="uq_thread_queued_submissions_consumed_run"),
        sa.UniqueConstraint("organization_id", "id", name="uq_thread_queued_submissions_organization_id"),
    )
    op.create_index(
        "ix_thread_queued_submissions_consumed",
        "thread_queued_submissions",
        ["organization_id", "thread_id", "consumed_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_thread_queued_submissions_failed",
        "thread_queued_submissions",
        ["organization_id", "thread_id", "failed_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_thread_queued_submissions_live",
        "thread_queued_submissions",
        ["organization_id", "thread_id", "position", "id"],
        unique=False,
        postgresql_where=sa.text("position IS NOT NULL"),
    )
    op.create_index(
        "uq_thread_queued_submissions_position",
        "thread_queued_submissions",
        ["organization_id", "thread_id", "position"],
        unique=True,
        postgresql_where=sa.text("position IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    op.drop_index(
        "uq_thread_queued_submissions_position",
        table_name="thread_queued_submissions",
        postgresql_where=sa.text("position IS NOT NULL"),
    )
    op.drop_index(
        "ix_thread_queued_submissions_live",
        table_name="thread_queued_submissions",
        postgresql_where=sa.text("position IS NOT NULL"),
    )
    op.drop_index("ix_thread_queued_submissions_failed", table_name="thread_queued_submissions")
    op.drop_index("ix_thread_queued_submissions_consumed", table_name="thread_queued_submissions")
    op.drop_table("thread_queued_submissions")
    op.drop_index("ix_thread_inbox_waiting_source", table_name="thread_inbox")
    op.drop_index("ix_thread_inbox_target", table_name="thread_inbox")
    op.drop_index("ix_thread_inbox_origin", table_name="thread_inbox")
    op.drop_index("ix_thread_inbox_kind_scan", table_name="thread_inbox")
    op.drop_index("ix_thread_inbox_fifo", table_name="thread_inbox")
    op.drop_table("thread_inbox")
    op.drop_table("thread_inbox_counters")
