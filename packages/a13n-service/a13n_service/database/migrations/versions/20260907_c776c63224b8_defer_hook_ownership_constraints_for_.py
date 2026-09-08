"""defer hook ownership constraints for atomic retention.

Revision ID: c776c63224b8
Revises: b1fb6c9be012
Create Date: 2026-09-07 09:16:51.226638+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c776c63224b8"
down_revision: str | Sequence[str] | None = "b1fb6c9be012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Defer the ownership cycle without weakening commit-time referential integrity.

    PostgreSQL takes bounded DDL locks and validates both foreign keys; no data
    backfill is required and older writers remain valid. SQLite rebuilds the two
    tables with foreign keys disabled by the migration runner, preserving triggers.
    """
    _replace_ownership(deferred=True)


def downgrade() -> None:
    """Restore immediate delete checks; committed rows already satisfy the same FKs."""
    _replace_ownership(deferred=False)


def _replace_ownership(*, deferred: bool) -> None:
    connection = op.get_bind()
    triggers = ()
    if connection.dialect.name == "sqlite":
        triggers = tuple(
            connection.execute(
                sa.text(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name IN ('hook_subscriptions', 'hook_subscription_revisions') ORDER BY name"
                )
            )
        )
        for name, _ in triggers:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    with op.batch_alter_table("hook_subscription_revisions") as batch:
        name = op.f("fk_hook_subscription_revisions_hook_subscription_id_hook_subscriptions")
        batch.drop_constraint(name, type_="foreignkey")
        batch.create_foreign_key(
            name,
            "hook_subscriptions",
            ["hook_subscription_id", "organization_id", "workspace_id"],
            ["id", "organization_id", "workspace_id"],
            ondelete="NO ACTION" if deferred else "RESTRICT",
            initially="DEFERRED" if deferred else None,
            deferrable=True if deferred else None,
        )
    with op.batch_alter_table("hook_subscriptions") as batch:
        batch.drop_constraint("fk_hook_subscriptions_current_revision", type_="foreignkey")
        batch.create_foreign_key(
            "fk_hook_subscriptions_current_revision",
            "hook_subscription_revisions",
            ["current_revision_id", "id", "organization_id", "workspace_id"],
            ["id", "hook_subscription_id", "organization_id", "workspace_id"],
            ondelete="NO ACTION" if deferred else "RESTRICT",
            initially="DEFERRED",
            deferrable=True,
        )
    for _, statement in triggers:
        connection.exec_driver_sql(statement)
