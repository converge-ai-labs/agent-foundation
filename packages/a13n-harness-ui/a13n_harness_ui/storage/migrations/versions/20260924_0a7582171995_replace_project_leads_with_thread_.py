"""replace project leads with thread coordinators.

Revision ID: 0a7582171995
Revises: 78e4e7206898
Create Date: 2026-09-24 02:48:13.375332+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a7582171995"
down_revision: str | Sequence[str] | None = "78e4e7206898"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "coordinator",
        sa.Column("thread_id", sa.String(length=80), nullable=False),
        sa.Column("auto_followup", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["thread.thread_id"], name=op.f("fk_coordinator_thread_id_thread"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("thread_id", name=op.f("pk_coordinator")),
    )
    op.create_table(
        "coordinator_worker",
        sa.Column("worker_thread_id", sa.String(length=80), nullable=False),
        sa.Column("coordinator_thread_id", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["coordinator_thread_id"],
            ["coordinator.thread_id"],
            name=op.f("fk_coordinator_worker_coordinator_thread_id_coordinator"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["worker_thread_id"],
            ["thread.thread_id"],
            name=op.f("fk_coordinator_worker_worker_thread_id_thread"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("worker_thread_id", name=op.f("pk_coordinator_worker")),
    )
    with op.batch_alter_table("coordinator_worker", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_coordinator_worker_coordinator_thread_id"), ["coordinator_thread_id"], unique=False
        )

    op.execute("INSERT INTO coordinator (thread_id, auto_followup) SELECT thread_id, enabled FROM project_lead")
    op.execute(
        "INSERT INTO coordinator_worker (worker_thread_id, coordinator_thread_id) SELECT worker_thread_id, lead_thread_id FROM project_lead_worker"
    )

    with op.batch_alter_table("project_lead_worker", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_project_lead_worker_lead_thread_id"))

    op.drop_table("project_lead_worker")
    op.drop_table("project_lead")


def downgrade() -> None:
    """Never collapse multiple Coordinators or discard immutable ownership."""
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM coordinator")):
        raise RuntimeError("Cannot downgrade while Coordinator roles exist.")
    op.create_table(
        "project_lead",
        sa.Column("project_id", sa.VARCHAR(length=128), nullable=False),
        sa.Column("thread_id", sa.VARCHAR(length=80), nullable=False),
        sa.Column("enabled", sa.BOOLEAN(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["thread.thread_id"], name=op.f("fk_project_lead_thread_id_thread"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_project_lead")),
        sa.UniqueConstraint("thread_id", name=op.f("uq_project_lead_thread_id")),
    )
    op.create_table(
        "project_lead_worker",
        sa.Column("worker_thread_id", sa.VARCHAR(length=80), nullable=False),
        sa.Column("lead_thread_id", sa.VARCHAR(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_thread_id"],
            ["project_lead.thread_id"],
            name=op.f("fk_project_lead_worker_lead_thread_id_project_lead"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["worker_thread_id"],
            ["thread.thread_id"],
            name=op.f("fk_project_lead_worker_worker_thread_id_thread"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("worker_thread_id", name=op.f("pk_project_lead_worker")),
    )
    with op.batch_alter_table("project_lead_worker", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_project_lead_worker_lead_thread_id"), ["lead_thread_id"], unique=False)

    with op.batch_alter_table("coordinator_worker", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_coordinator_worker_coordinator_thread_id"))

    op.drop_table("coordinator_worker")
    op.drop_table("coordinator")
