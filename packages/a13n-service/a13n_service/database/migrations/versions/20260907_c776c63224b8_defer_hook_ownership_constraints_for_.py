"""defer hook ownership constraints for atomic retention.

Revision ID: c776c63224b8
Revises: b1fb6c9be012
Create Date: 2026-09-07 09:16:51.226638+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c776c63224b8"
down_revision: str | Sequence[str] | None = "b1fb6c9be012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Defer the ownership cycle without weakening commit-time referential integrity.

    PostgreSQL takes bounded DDL locks and validates both foreign keys; no data
    backfill is required and older writers remain valid.
    """
    _replace_ownership(deferred=True)


def downgrade() -> None:
    """Restore immediate delete checks; committed rows already satisfy the same FKs."""
    _replace_ownership(deferred=False)


def _replace_ownership(*, deferred: bool) -> None:
    revisions_fk = op.f("fk_hook_subscription_revisions_hook_subscription_id_hook_subscriptions")
    op.drop_constraint(revisions_fk, "hook_subscription_revisions", type_="foreignkey")
    op.create_foreign_key(
        revisions_fk,
        "hook_subscription_revisions",
        "hook_subscriptions",
        ["hook_subscription_id", "organization_id", "workspace_id"],
        ["id", "organization_id", "workspace_id"],
        ondelete="NO ACTION" if deferred else "RESTRICT",
        initially="DEFERRED" if deferred else None,
        deferrable=True if deferred else None,
    )
    op.drop_constraint("fk_hook_subscriptions_current_revision", "hook_subscriptions", type_="foreignkey")
    op.create_foreign_key(
        "fk_hook_subscriptions_current_revision",
        "hook_subscriptions",
        "hook_subscription_revisions",
        ["current_revision_id", "id", "organization_id", "workspace_id"],
        ["id", "hook_subscription_id", "organization_id", "workspace_id"],
        ondelete="NO ACTION" if deferred else "RESTRICT",
        initially="DEFERRED",
        deferrable=True,
    )
