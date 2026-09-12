"""move inbox accounting onto threads.

Revision ID: 56b4ee866468
Revises: f6ec04da96ed
Create Date: 2026-09-12 02:00:18.455135+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "56b4ee866468"
down_revision: str | Sequence[str] | None = "f6ec04da96ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Old and new binaries write different accounting owners. Quiesce all Service
# writers before upgrade/downgrade and restart only the matching build afterward.
# PostgreSQL takes bounded ACCESS EXCLUSIVE locks and copies one row per Thread;
# migration lock/statement timeouts bound waiting and the backfill. No new index
# is needed. SQLite uses native column DDL, avoiding a rebuild of the referenced
# Thread table. Both directions preserve high-water marks even after inbox GC.
_COLUMNS = (
    ("next_delivery_sequence", "1", "next_delivery_sequence >= 1", "positive"),
    ("pending_count", "0", "pending_count >= 0", "non_negative"),
    ("pending_bytes", "0", "pending_bytes >= 0", "non_negative"),
)


def upgrade() -> None:
    """Move existing accounting before dropping its old owner."""
    connection = op.get_bind()
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        # pysqlite's legacy transaction mode does not begin for DDL. Include
        # column changes in the same rollback boundary as backfill and stamping.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        connection.exec_driver_sql("LOCK TABLE threads, thread_inbox_counters IN ACCESS EXCLUSIVE MODE")
    missing = connection.scalar(
        sa.text(
            "SELECT count(*) FROM threads t WHERE NOT EXISTS "
            "(SELECT 1 FROM thread_inbox_counters c "
            "WHERE c.organization_id = t.organization_id AND c.thread_id = t.id)"
        )
    )
    if missing:
        raise RuntimeError("Cannot migrate Thread inbox accounting: a Thread counter is missing")
    for name, default, condition, suffix in _COLUMNS:
        constraint = op.f(f"ck_threads_{name}_{suffix}")
        # Inline checks let SQLite add a column without rebuilding threads and
        # cascading through its many inbound references.
        checks = (sa.CheckConstraint(condition, name=constraint),) if sqlite else ()
        op.add_column("threads", sa.Column(name, sa.BigInteger(), *checks, server_default=default, nullable=False))
        if not sqlite:
            op.create_check_constraint(constraint, "threads", condition)
    connection.execute(
        sa.text(
            "UPDATE threads SET (next_delivery_sequence, pending_count, pending_bytes) = "
            "(SELECT c.next_delivery_sequence, c.pending_count, c.pending_bytes "
            "FROM thread_inbox_counters c "
            "WHERE c.organization_id = threads.organization_id AND c.thread_id = threads.id)"
        )
    )
    op.drop_table("thread_inbox_counters")


def downgrade() -> None:
    """Restore the old owner and its current values before removing columns."""
    connection = op.get_bind()
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        connection.exec_driver_sql("LOCK TABLE threads IN ACCESS EXCLUSIVE MODE")
    op.create_table(
        "thread_inbox_counters",
        sa.Column("thread_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("next_delivery_sequence", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("pending_count", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("pending_bytes", sa.BIGINT(), autoincrement=False, nullable=False),
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
        sa.UniqueConstraint(
            "organization_id",
            "thread_id",
            name=op.f("uq_thread_inbox_counters_scope"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    connection.execute(
        sa.text(
            "INSERT INTO thread_inbox_counters "
            "(thread_id, organization_id, next_delivery_sequence, pending_count, pending_bytes) "
            "SELECT id, organization_id, next_delivery_sequence, pending_count, pending_bytes FROM threads"
        )
    )
    for name, _, _, suffix in reversed(_COLUMNS):
        if not sqlite:
            op.drop_constraint(op.f(f"ck_threads_{name}_{suffix}"), "threads", type_="check")
        op.drop_column("threads", name)
