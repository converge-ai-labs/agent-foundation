"""add oidc identities and grants

Revision ID: 5d3f6246fcd7
Revises: ddc9c71ce4de
"""

import sqlalchemy as sa
from alembic import op

revision = "5d3f6246fcd7"
down_revision = "ddc9c71ce4de"
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
    op.create_table(
        "oidc_grants",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("principal_id", sa.String(length=72), nullable=False),
        sa.Column("provider_id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name=op.f("fk_oidc_grants_organization_id_workspaces"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_oidc_grants_organization_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principals.id"], name=op.f("fk_oidc_grants_principal_id_principals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oidc_grants")),
        sa.UniqueConstraint(
            "principal_id",
            "provider_id",
            "organization_id",
            "workspace_id",
            name=op.f("uq_oidc_grants_principal_id_provider_id_organization_id_workspace_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_oidc_grants_principal_provider", "oidc_grants", ["principal_id", "provider_id"], unique=False)
    op.create_index(
        "ix_oidc_grants_scope", "oidc_grants", ["organization_id", "workspace_id", "principal_id"], unique=False
    )
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
    op.drop_index("ix_oidc_grants_scope", table_name="oidc_grants")
    op.drop_index("ix_oidc_grants_principal_provider", table_name="oidc_grants")
    op.drop_table("oidc_grants")
