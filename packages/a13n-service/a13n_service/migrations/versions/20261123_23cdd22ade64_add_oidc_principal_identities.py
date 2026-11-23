"""add oidc principal identities

Revision ID: 23cdd22ade64
Revises: 3d51b520f762
"""

import sqlalchemy as sa
from alembic import op

revision = "23cdd22ade64"
down_revision = "3d51b520f762"
branch_labels = None
depends_on = None


def _replace_principal_guard(*, oidc: bool) -> None:
    new = ", NEW.oidc_issuer, NEW.oidc_subject" if oidc else ""
    old = ", OLD.oidc_issuer, OLD.oidc_subject" if oidc else ""
    op.execute(f"""
        CREATE OR REPLACE FUNCTION guard_principal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.id, NEW.kind, NEW.home_workspace_id, NEW.created_at{new})
                IS DISTINCT FROM ROW(OLD.id, OLD.kind, OLD.home_workspace_id, OLD.created_at{old})
            THEN RAISE EXCEPTION 'principal identity and home workspace are immutable'; END IF;
            RETURN NEW;
        END $$
    """)


def upgrade() -> None:
    op.add_column("principals", sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.add_column("principals", sa.Column("oidc_subject", sa.String(), nullable=True))
    op.add_column("principals", sa.Column("oidc_email", sa.String(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_principals_oidc_issuer_oidc_subject"), "principals", ["oidc_issuer", "oidc_subject"]
    )
    op.drop_constraint(op.f("ck_principals_user_email"), "principals", type_="check")
    op.create_check_constraint(
        op.f("ck_principals_sign_in_identity"),
        "principals",
        "(oidc_issuer IS NULL) = (oidc_subject IS NULL) AND (oidc_email IS NULL OR oidc_issuer IS NOT NULL) AND ((kind = 'user' AND (email IS NOT NULL) <> (oidc_issuer IS NOT NULL)) OR (kind = 'service_account' AND email IS NULL AND oidc_issuer IS NULL))",
    )
    _replace_principal_guard(oidc=True)


def downgrade() -> None:
    # The old email constraint cannot represent provisioned OIDC users.
    _replace_principal_guard(oidc=False)
    op.drop_constraint(op.f("ck_principals_sign_in_identity"), "principals", type_="check")
    op.create_check_constraint(
        op.f("ck_principals_user_email"), "principals", "(kind::text = 'user'::text) = (email IS NOT NULL)"
    )
    op.drop_constraint(op.f("uq_principals_oidc_issuer_oidc_subject"), "principals", type_="unique")
    op.drop_column("principals", "oidc_email")
    op.drop_column("principals", "oidc_subject")
    op.drop_column("principals", "oidc_issuer")
