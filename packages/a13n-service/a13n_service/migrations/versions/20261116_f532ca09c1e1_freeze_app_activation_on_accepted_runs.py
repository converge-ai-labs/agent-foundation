"""freeze app activation on accepted runs

Revision ID: f532ca09c1e1
Revises: 29fafe2d9014
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f532ca09c1e1"
down_revision = "29fafe2d9014"
branch_labels = None
depends_on = None

# Frozen from the generated model rule; migration history never imports current ORM metadata.
_STAMP_COLUMNS = (
    "id organization_id workspace_id session_id thread_id agent_id agent_revision_id revision_selection "
    "principal_id authority options app_activation options_digest mcp_headers environment_mounts browser_binding "
    "memory_mounts source_entry_id occurrence_trigger_entry_id resume resumed_by_id request_key request_digest "
    "trigger lineage parent_run_id status wait_reason pending cancel_requested_at current_attempt_id available_at "
    "attempts max_attempts output failure usage_at_seal labels started_at sealed_at version created_at updated_at"
).split()


def _stamp(*, activation: bool) -> None:
    names = [name for name in _STAMP_COLUMNS if activation or name != "app_activation"]
    old = ", ".join(f'OLD."{name}"' for name in names)
    new = ", ".join(f'NEW."{name}"' for name in names)
    op.execute("DROP TRIGGER stamp_resource ON runs")
    op.execute(
        f"CREATE TRIGGER stamp_resource BEFORE UPDATE ON runs FOR EACH ROW "
        f"WHEN (ROW({old}) IS DISTINCT FROM ROW({new})) EXECUTE FUNCTION stamp_resource()"
    )


def upgrade() -> None:
    # Additive constant default: old Runs retain empty activation, without policy backfill.
    # The bounded table lock and trigger replacement share this migration's transaction.
    op.add_column(
        "runs",
        sa.Column(
            "app_activation",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    _stamp(activation=True)


def downgrade() -> None:
    _stamp(activation=False)
    op.drop_column("runs", "app_activation")
