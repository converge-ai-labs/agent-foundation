"""track App ownership of egress credentials

Revision ID: ddc9c71ce4de
Revises: 3d51b520f762
"""

import sqlalchemy as sa
from alembic import op

revision = "ddc9c71ce4de"
down_revision = "3d51b520f762"
branch_labels = None
depends_on = None


_GUARD = """
            CREATE OR REPLACE FUNCTION guard_egress_credential() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.owner_app_id IS DISTINCT FROM OLD.owner_app_id
                THEN RAISE EXCEPTION 'credential App ownership is immutable'; END IF;
                IF NEW.allowed_hosts IS DISTINCT FROM OLD.allowed_hosts
                THEN RAISE EXCEPTION 'credential injection hosts are immutable'; END IF;
                IF OLD.state = 'revoked' AND ROW(NEW.state, NEW.value, NEW.revoked_at)
                    IS DISTINCT FROM ROW(OLD.state, OLD.value, OLD.revoked_at)
                THEN RAISE EXCEPTION 'credential revocation is final'; END IF;
                IF NEW.credential_version < OLD.credential_version
                    OR NEW.credential_version > OLD.credential_version + 1
                    OR (NEW.state IS DISTINCT FROM OLD.state AND NEW.credential_version <> OLD.credential_version + 1)
                THEN RAISE EXCEPTION 'invalid credential version transition'; END IF;
                RETURN NEW;
            END $$
"""


def upgrade() -> None:
    op.add_column("egress_credentials", sa.Column("owner_app_id", sa.String(length=128), nullable=True))
    op.execute(_GUARD)


def downgrade() -> None:
    op.execute(
        _GUARD.replace(
            "                IF NEW.owner_app_id IS DISTINCT FROM OLD.owner_app_id\n                THEN RAISE EXCEPTION 'credential App ownership is immutable'; END IF;\n",
            "",
        )
    )
    op.drop_column("egress_credentials", "owner_app_id")
