"""separate thread session bindings from directory views

Revision ID: ab5919040d56
Revises: f6f3e4930b08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ab5919040d56"
down_revision = "f6f3e4930b08"
branch_labels = None
depends_on = None


def _require_settled() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM runs WHERE status IN ('accepted', 'running', 'waiting')"
            " AND environment_mounts <> '[]'::jsonb) OR EXISTS"
            " (SELECT 1 FROM tool_executions WHERE completed_at IS NULL"
            " AND (environment_mounts <> '[]'::jsonb OR shell IS NOT NULL))"
        )
    ):
        raise RuntimeError("Settle all admitted environment work and stop writers before the Session binding cutover")


def upgrade() -> None:
    _require_settled()
    op.create_table(
        "thread_session_bindings",
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("environment_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("head", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name=op.f("fk_thread_session_bindings_organization_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_thread_session_bindings_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.id"],
            name=op.f("fk_thread_session_bindings_workspace_id_environments"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "thread_id"],
            ["threads.workspace_id", "threads.id"],
            name=op.f("fk_thread_session_bindings_workspace_id_threads"),
        ),
        sa.PrimaryKeyConstraint("thread_id", "environment_id", "name", name=op.f("pk_thread_session_bindings")),
    )
    op.add_column(
        "thread_environments",
        sa.Column("session_binding", sa.String(), server_default=sa.text("'default'"), nullable=False),
    )
    op.drop_constraint(op.f("uq_thread_environments_thread_id_environment_id"), "thread_environments", type_="unique")
    op.execute(
        "INSERT INTO thread_session_bindings"
        " (thread_id, environment_id, name, organization_id, workspace_id, configuration)"
        " SELECT thread_id, environment_id, 'default', organization_id, workspace_id, '{}'::jsonb"
        " FROM thread_environments"
    )
    # Old per-view/full-Run hashes are not new logical bindings. Keep sealed Runs and tool history intact.
    op.drop_column("thread_execution_contexts", "sessions")
    op.execute("UPDATE thread_execution_contexts SET worker_id = NULL, generation = generation + 1")


def downgrade() -> None:
    _require_settled()
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM thread_session_bindings"
            " WHERE name <> 'default' OR jsonb_strip_nulls(configuration) <> '{}'::jsonb)"
            " OR EXISTS (SELECT 1 FROM thread_environments GROUP BY thread_id, environment_id HAVING count(*) > 1)"
        )
    ):
        raise RuntimeError("Configured Session bindings or shared directory views prevent a lossless downgrade")
    op.add_column(
        "thread_execution_contexts",
        sa.Column(
            "sessions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.execute("UPDATE thread_execution_contexts SET worker_id = NULL, generation = generation + 1")
    op.create_unique_constraint(
        op.f("uq_thread_environments_thread_id_environment_id"),
        "thread_environments",
        ["thread_id", "environment_id"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("thread_environments", "session_binding")
    op.drop_table("thread_session_bindings")
