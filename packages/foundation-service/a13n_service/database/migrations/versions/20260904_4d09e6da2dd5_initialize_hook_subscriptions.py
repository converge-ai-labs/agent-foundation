"""initialize hook subscriptions.

Revision ID: 4d09e6da2dd5
Revises: 69a28e8783ad
Create Date: 2026-09-04 08:21:59.444459+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4d09e6da2dd5"
down_revision: str | Sequence[str] | None = "69a28e8783ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the domain schema."""
    op.create_table(
        "hook_subscriptions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("current_revision_id", sa.String(length=72), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("inline_run_id", sa.String(length=72), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("updated_by_type", sa.String(length=32), nullable=False),
        sa.Column("updated_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')", name=op.f("ck_hook_subscriptions_created_by_type_valid")
        ),
        sa.CheckConstraint(
            "updated_by_type IN ('user', 'service_account')", name=op.f("ck_hook_subscriptions_updated_by_type_valid")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_hook_subscriptions_version_positive")),
        sa.ForeignKeyConstraint(
            ["current_revision_id", "id", "organization_id", "workspace_id"],
            [
                "hook_subscription_revisions.id",
                "hook_subscription_revisions.hook_subscription_id",
                "hook_subscription_revisions.organization_id",
                "hook_subscription_revisions.workspace_id",
            ],
            name="fk_hook_subscriptions_current_revision",
            ondelete="RESTRICT",
            initially="DEFERRED",
            deferrable=True,
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name=op.f("fk_hook_subscriptions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hook_subscriptions")),
        sa.UniqueConstraint("id", "organization_id", "workspace_id", name="uq_hook_subscriptions_organization_id"),
        sa.UniqueConstraint("organization_id", "inline_run_id", name="uq_hook_subscriptions_inline_run"),
    )
    op.create_index(
        "ix_hook_subscriptions_active_workspace",
        "hook_subscriptions",
        ["organization_id", "workspace_id", "id"],
        unique=False,
        postgresql_where=sa.text("enabled AND deleted_at IS NULL"),
        sqlite_where=sa.text("enabled = 1 AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_hook_subscriptions_workspace_updated",
        "hook_subscriptions",
        ["organization_id", "workspace_id", "updated_at", "id"],
        unique=False,
    )
    op.create_table(
        "hook_subscription_revisions",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("hook_subscription_id", sa.String(length=72), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "hook_names", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False
        ),
        sa.Column("session_id", sa.String(length=72), nullable=True),
        sa.Column("thread_id", sa.String(length=72), nullable=True),
        sa.Column("run_id", sa.String(length=72), nullable=True),
        sa.Column("endpoint_url", sa.String(length=8192), nullable=False),
        sa.Column("signing_secret_id", sa.String(length=72), nullable=False),
        sa.Column("signature_profile", sa.String(length=32), nullable=False),
        sa.Column("created_by_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_id", sa.String(length=72), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_by_type IN ('user', 'service_account')",
            name=op.f("ck_hook_subscription_revisions_created_by_type_valid"),
        ),
        sa.CheckConstraint(
            "signature_profile = 'hmac_sha256_v1'", name=op.f("ck_hook_subscription_revisions_signature_profile_valid")
        ),
        sa.CheckConstraint(
            "length(endpoint_url) BETWEEN 1 AND 8192", name=op.f("ck_hook_subscription_revisions_endpoint_url_bounded")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_hook_subscription_revisions_version_positive")),
        sa.ForeignKeyConstraint(
            ["hook_subscription_id", "organization_id", "workspace_id"],
            ["hook_subscriptions.id", "hook_subscriptions.organization_id", "hook_subscriptions.workspace_id"],
            name=op.f("fk_hook_subscription_revisions_hook_subscription_id_hook_subscriptions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name=op.f("fk_hook_subscription_revisions_organization_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "session_id"],
            ["sessions.organization_id", "sessions.id"],
            name=op.f("fk_hook_subscription_revisions_organization_id_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["threads.organization_id", "threads.id"],
            name=op.f("fk_hook_subscription_revisions_organization_id_threads"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signing_secret_id"],
            ["secrets.id"],
            name=op.f("fk_hook_subscription_revisions_signing_secret_id_secrets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hook_subscription_revisions")),
        sa.UniqueConstraint("hook_subscription_id", "version", name="uq_hook_subscription_revisions_number"),
        sa.UniqueConstraint(
            "id",
            "hook_subscription_id",
            "organization_id",
            "workspace_id",
            name="uq_hook_subscription_revisions_head_authority",
        ),
    )
    op.create_index(
        "ix_hook_subscription_revisions_head",
        "hook_subscription_revisions",
        ["hook_subscription_id", "version", "id"],
        unique=False,
    )
    op.create_index(
        "ix_hook_subscription_revisions_hook_names",
        "hook_subscription_revisions",
        ["hook_names"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_hook_subscription_revisions_run",
        "hook_subscription_revisions",
        ["organization_id", "run_id", "id"],
        unique=False,
        postgresql_where=sa.text("run_id IS NOT NULL"),
        sqlite_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_hook_subscription_revisions_session",
        "hook_subscription_revisions",
        ["organization_id", "session_id", "id"],
        unique=False,
        postgresql_where=sa.text("session_id IS NOT NULL"),
        sqlite_where=sa.text("session_id IS NOT NULL"),
    )
    op.create_index(
        "ix_hook_subscription_revisions_thread",
        "hook_subscription_revisions",
        ["organization_id", "thread_id", "id"],
        unique=False,
        postgresql_where=sa.text("thread_id IS NOT NULL"),
        sqlite_where=sa.text("thread_id IS NOT NULL"),
    )
    op.create_index(
        "uq_hook_subscription_revisions_id_organization",
        "hook_subscription_revisions",
        ["id", "organization_id", "workspace_id"],
        unique=True,
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_hook_subscriptions_current_revision",
            "hook_subscriptions",
            "hook_subscription_revisions",
            ["current_revision_id", "id", "organization_id", "workspace_id"],
            ["id", "hook_subscription_id", "organization_id", "workspace_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )
    _create_revision_guards()


def downgrade() -> None:
    """Remove the domain schema in reverse dependency order."""
    _drop_revision_guards()
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_hook_subscriptions_current_revision", "hook_subscriptions", type_="foreignkey")
    op.drop_index("uq_hook_subscription_revisions_id_organization", table_name="hook_subscription_revisions")
    op.drop_index(
        "ix_hook_subscription_revisions_thread",
        table_name="hook_subscription_revisions",
        postgresql_where=sa.text("thread_id IS NOT NULL"),
        sqlite_where=sa.text("thread_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_hook_subscription_revisions_session",
        table_name="hook_subscription_revisions",
        postgresql_where=sa.text("session_id IS NOT NULL"),
        sqlite_where=sa.text("session_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_hook_subscription_revisions_run",
        table_name="hook_subscription_revisions",
        postgresql_where=sa.text("run_id IS NOT NULL"),
        sqlite_where=sa.text("run_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_hook_subscription_revisions_hook_names", table_name="hook_subscription_revisions", postgresql_using="gin"
    )
    op.drop_index("ix_hook_subscription_revisions_head", table_name="hook_subscription_revisions")
    op.drop_table("hook_subscription_revisions")
    op.drop_index("ix_hook_subscriptions_workspace_updated", table_name="hook_subscriptions")
    op.drop_index(
        "ix_hook_subscriptions_active_workspace",
        table_name="hook_subscriptions",
        postgresql_where=sa.text("enabled AND deleted_at IS NULL"),
        sqlite_where=sa.text("enabled = 1 AND deleted_at IS NULL"),
    )
    op.drop_table("hook_subscriptions")


def _create_revision_guards() -> None:
    columns = (
        "id",
        "organization_id",
        "workspace_id",
        "hook_subscription_id",
        "version",
        "hook_names",
        "session_id",
        "thread_id",
        "run_id",
        "endpoint_url",
        "signing_secret_id",
        "signature_profile",
        "created_by_type",
        "created_by_id",
        "created_at",
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint(
            "hook_names_bounded",
            "hook_subscription_revisions",
            "jsonb_typeof(hook_names) = 'array' AND jsonb_array_length(hook_names) BETWEEN 1 AND 128",
        )
        changed = " OR ".join(f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in columns)
        op.execute(
            """
            CREATE FUNCTION validate_hook_subscription_revision_insert()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF jsonb_typeof(NEW.hook_names) <> 'array'
                   OR jsonb_array_length(NEW.hook_names) NOT BETWEEN 1 AND 128 THEN
                    RAISE EXCEPTION 'HookSubscription hook names must be a bounded array';
                END IF;
                IF EXISTS (
                    SELECT value
                    FROM jsonb_array_elements_text(NEW.hook_names)
                    GROUP BY value
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION 'HookSubscription hook names must be unique';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER validate_hook_subscription_revision_insert
            BEFORE INSERT ON hook_subscription_revisions
            FOR EACH ROW EXECUTE FUNCTION validate_hook_subscription_revision_insert()
            """
        )
        op.execute(
            f"""
            CREATE FUNCTION reject_hook_subscription_revision_update()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF {changed} THEN
                    RAISE EXCEPTION 'HookSubscription Revision columns are immutable';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER reject_hook_subscription_revision_update
            BEFORE UPDATE ON hook_subscription_revisions
            FOR EACH ROW EXECUTE FUNCTION reject_hook_subscription_revision_update()
            """
        )
        return
    op.execute(
        """
        CREATE TRIGGER validate_hook_subscription_revision_insert
        BEFORE INSERT ON hook_subscription_revisions
        WHEN json_valid(NEW.hook_names) = 0
             OR json_type(NEW.hook_names) <> 'array'
             OR json_array_length(NEW.hook_names) NOT BETWEEN 1 AND 128
             OR EXISTS (
                 SELECT value
                 FROM json_each(NEW.hook_names)
                 GROUP BY value
                 HAVING count(*) > 1
             )
        BEGIN
            SELECT RAISE(ABORT, 'HookSubscription hook names must be a bounded unique array');
        END
        """
    )
    changed = " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in columns)
    op.execute(
        f"""
        CREATE TRIGGER reject_hook_subscription_revision_update
        BEFORE UPDATE ON hook_subscription_revisions
        WHEN {changed}
        BEGIN
            SELECT RAISE(ABORT, 'HookSubscription Revision columns are immutable');
        END
        """
    )


def _drop_revision_guards() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER validate_hook_subscription_revision_insert ON hook_subscription_revisions")
        op.execute("DROP FUNCTION validate_hook_subscription_revision_insert()")
        op.execute("DROP TRIGGER reject_hook_subscription_revision_update ON hook_subscription_revisions")
        op.execute("DROP FUNCTION reject_hook_subscription_revision_update()")
        return
    op.execute("DROP TRIGGER validate_hook_subscription_revision_insert")
    op.execute("DROP TRIGGER reject_hook_subscription_revision_update")
