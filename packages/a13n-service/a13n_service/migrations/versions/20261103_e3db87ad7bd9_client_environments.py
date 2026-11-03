"""register client environments and connection credentials

Revision ID: e3db87ad7bd9
Revises: 91abbf7b03c8

The atomic DDL takes the environment table lock, scans it for the partial
index and new checks, and backfills only existing HTTP targets. The constant
managed default does not rewrite managed rows. The migration lock/statement
timeouts bound contention; failure rolls back the revision before a retry.
Legacy inserts remain compatible, but register clients only after old Workers
and Controls have drained. Roll forward once registrations exist, including
tombstones; a downgrade cannot preserve their execution authority.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e3db87ad7bd9"
down_revision = "91abbf7b03c8"
branch_labels = None
depends_on = None


_OLD_CHECKS = {
    "status": "status IN ('reserved', 'creating', 'starting', 'ready', 'stopping', 'stopped', 'deleting', 'deleted')",
    "connect_only": "template_id IS NOT NULL OR status IN ('ready', 'deleted')",
    "managed": "(template_id IS NULL) = (provider_id IS NULL)",
    "external": "(template_id IS NULL) = (device_id IS NOT NULL) AND (template_id IS NULL) = (endpoint IS NOT NULL) AND (template_id IS NOT NULL OR (provider_identity IS NULL AND handle IS NULL))",
    "token": "(token IS NOT NULL) = (template_id IS NULL AND status <> 'deleted')",
}
_NEW_CHECKS = {
    "status": "status IN ('reserved', 'creating', 'starting', 'ready', 'stopping', 'stopped', 'deleting', 'deleted', 'disabled')",
    "connect_only": "kind = 'managed' OR status IN ('ready', 'disabled', 'deleted')",
    "managed": "(kind = 'managed') = (template_id IS NOT NULL) AND (template_id IS NULL) = (provider_id IS NULL)",
    "external": "(kind <> 'managed') = (device_id IS NOT NULL) AND (kind = 'external_http') = (endpoint IS NOT NULL) AND (kind = 'managed' OR (provider_identity IS NULL AND handle IS NULL)) AND (kind <> 'client_envd' OR owner_principal_id IS NOT NULL)",
    "token": "(token IS NOT NULL) = (kind = 'external_http' AND status <> 'deleted')",
}


def _replace_checks(checks: dict[str, str]) -> None:
    for key, expression in checks.items():
        name = op.f(f"ck_environments_{key}")
        op.drop_constraint(name, "environments", type_="check")
        op.create_check_constraint(name, "environments", expression)


def upgrade() -> None:
    op.create_table(
        "client_environment_settings",
        sa.Column("environment_id", sa.String(length=72), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("default_working_directory", sa.String(), nullable=False),
        sa.Column("permission_ceiling", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("declared_capabilities", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.CheckConstraint("credential_version > 0", name=op.f("ck_client_environment_settings_credential_version")),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environments.id"],
            name=op.f("fk_client_environment_settings_environment_id_environments"),
        ),
        sa.PrimaryKeyConstraint("environment_id", name=op.f("pk_client_environment_settings")),
    )
    # The constant default assigns existing managed rows without rewriting the table.
    # Only the old HTTP registrations, including tombstones, need a physical update.
    op.add_column("environments", sa.Column("kind", sa.String(length=24), nullable=False, server_default="managed"))
    op.alter_column("environments", "kind", server_default=None)
    op.execute("ALTER TABLE environments DISABLE TRIGGER stamp_resource")
    op.execute("UPDATE environments SET kind = 'external_http' WHERE endpoint IS NOT NULL")
    op.execute("ALTER TABLE environments ENABLE TRIGGER stamp_resource")
    _replace_checks(_NEW_CHECKS)
    op.create_index(
        "uq_environments_client_identity",
        "environments",
        ["workspace_id", "owner_principal_id", "device_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'client_envd' AND status <> 'deleted'"),
    )
    op.create_check_constraint(
        op.f("ck_environments_disabled"), "environments", "status <> 'disabled' OR kind = 'client_envd'"
    )
    op.create_check_constraint(
        op.f("ck_environments_kind"), "environments", "kind IN ('managed', 'external_http', 'client_envd')"
    )
    op.execute(
        "CREATE FUNCTION classify_legacy_environment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.template_id IS NOT NULL THEN NEW.kind := 'managed'; ELSIF NEW.endpoint IS NOT NULL THEN NEW.kind := 'external_http'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER classify_legacy_environment BEFORE INSERT ON environments FOR EACH ROW WHEN (NEW.kind IS NULL) EXECUTE FUNCTION classify_legacy_environment()"
    )
    op.execute(
        "CREATE TRIGGER guard_environment_target BEFORE UPDATE ON environments FOR EACH ROW WHEN (ROW(OLD.kind, OLD.device_id, OLD.owner_principal_id) IS DISTINCT FROM ROW(NEW.kind, NEW.device_id, NEW.owner_principal_id)) EXECUTE FUNCTION refuse_mutation()"
    )


def downgrade() -> None:
    # No older schema can represent a client registration, even a deleted one.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT FROM environments WHERE kind = 'client_envd')")):
        raise RuntimeError("Client registrations prevent downgrade")
    op.execute("DROP TRIGGER guard_environment_target ON environments")
    op.execute("DROP TRIGGER classify_legacy_environment ON environments")
    op.execute("DROP FUNCTION classify_legacy_environment()")
    _replace_checks(_OLD_CHECKS)
    op.drop_constraint(op.f("ck_environments_kind"), "environments", type_="check")
    op.drop_constraint(op.f("ck_environments_disabled"), "environments", type_="check")
    op.drop_index(
        "uq_environments_client_identity",
        table_name="environments",
        postgresql_where=sa.text("kind = 'client_envd' AND status <> 'deleted'"),
    )
    op.drop_column("environments", "kind")
    op.drop_table("client_environment_settings")
