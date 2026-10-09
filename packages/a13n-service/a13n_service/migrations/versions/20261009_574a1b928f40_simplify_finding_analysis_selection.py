"""simplify finding analysis selection

Revision ID: 574a1b928f40
Revises: 74b2192ae6ee

Transactional DDL takes a table lock and scans the new bounded analysis table to
backfill at most 20 selected entries per row. No new index is needed. Upgrade
API and Worker builds together; old builds cannot use the contracted schema.
A failed migration rolls back and can be retried. Prefer application rollback
or forward repair: downgrade reconstructs selection but loses coverage claims.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "574a1b928f40"
down_revision = "74b2192ae6ee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finding_analyses", sa.Column("selected_traces", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    # Preserve backend selection order and the trusted trace-to-Run mapping.
    op.execute(
        """
        UPDATE finding_analyses AS a SET selected_traces = (
            SELECT COALESCE(jsonb_agg(
                jsonb_build_object(
                    'trace_id', entry.trace_id, 'run_id', a.trace_runs ->> entry.trace_id
                ) ORDER BY entry.position
            ), '[]'::jsonb)
            FROM jsonb_array_elements_text(a.trace_ids) WITH ORDINALITY AS entry(trace_id, position)
        )
        """
    )
    op.alter_column("finding_analyses", "selected_traces", nullable=False)
    op.drop_constraint(op.f("fk_finding_analyses_thread_id_threads"), "finding_analyses", type_="foreignkey")
    op.drop_constraint(op.f("fk_finding_analyses_session_id_sessions"), "finding_analyses", type_="foreignkey")
    op.drop_column("finding_analyses", "reviewed_trace_ids")
    op.drop_column("finding_analyses", "trace_ids")
    op.drop_column("finding_analyses", "session_id")
    op.drop_column("finding_analyses", "thread_id")
    op.drop_column("finding_analyses", "reported")
    op.drop_column("finding_analyses", "limitations")
    op.drop_column("finding_analyses", "trace_runs")


def downgrade() -> None:
    # Selection and navigation can be reconstructed; discarded model coverage claims cannot.
    for name in ("trace_runs", "trace_ids", "reviewed_trace_ids"):
        op.add_column("finding_analyses", sa.Column(name, postgresql.JSONB(), nullable=True))
    for name in ("session_id", "thread_id"):
        op.add_column("finding_analyses", sa.Column(name, sa.String(72), nullable=True))
    op.add_column("finding_analyses", sa.Column("limitations", sa.String(), nullable=True))
    op.add_column("finding_analyses", sa.Column("reported", sa.Boolean(), nullable=True))
    op.execute(
        """
        UPDATE finding_analyses AS a SET
            trace_ids = (SELECT COALESCE(jsonb_agg(entry.value ->> 'trace_id' ORDER BY entry.position), '[]'::jsonb)
                FROM jsonb_array_elements(a.selected_traces) WITH ORDINALITY AS entry(value, position)),
            trace_runs = (SELECT COALESCE(jsonb_object_agg(entry ->> 'trace_id', entry ->> 'run_id'), '{}'::jsonb)
                FROM jsonb_array_elements(a.selected_traces) AS entry),
            session_id = r.session_id, thread_id = r.thread_id,
            reviewed_trace_ids = '[]'::jsonb, reported = false, limitations = ''
        FROM runs AS r WHERE r.id = a.run_id
        """
    )
    for name in ("trace_runs", "trace_ids", "reviewed_trace_ids", "session_id", "thread_id", "limitations", "reported"):
        op.alter_column("finding_analyses", name, nullable=False)
    op.create_foreign_key(
        op.f("fk_finding_analyses_session_id_sessions"), "finding_analyses", "sessions", ["session_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_finding_analyses_thread_id_threads"), "finding_analyses", "threads", ["thread_id"], ["id"]
    )
    op.drop_column("finding_analyses", "selected_traces")
