"""replace tool snapshots and catalogs with live MCP composition.

Revision ID: 93c2e720821c
Revises: 8c85e610ca5a
Create Date: 2026-09-04 09:40:18.951506+00:00
"""

from collections.abc import Sequence
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "93c2e720821c"
down_revision: str | Sequence[str] | None = "8c85e610ca5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Offline development replacement; retained Agent/Run contracts require reset."""
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM agent_revisions) OR EXISTS (SELECT 1 FROM runs)")):
        raise RuntimeError(
            "Reset development Agent/Run state before installing in-process MCP; old execution contracts are not supported."
        )

    with op.batch_alter_table("mcp_tool_catalogs") as batch:
        batch.drop_index(op.f("ix_mcp_tool_catalogs_latest"))
        batch.drop_index(op.f("ix_mcp_tool_catalogs_retention"))
        batch.drop_index(op.f("uq_mcp_tool_catalogs_digest"))
    op.drop_table("mcp_tool_catalogs")
    with op.batch_alter_table("connector_tool_catalogs") as batch:
        batch.drop_index(op.f("ix_connector_tool_catalogs_latest"))
        batch.drop_index(op.f("ix_connector_tool_catalogs_retention"))
        batch.drop_index(op.f("uq_connector_tool_catalogs_digest"))
    op.drop_table("connector_tool_catalogs")
    with op.batch_alter_table("connector_connections") as batch:
        batch.drop_constraint(op.f("ck_connector_connections_catalog_attempt_count_non_negative"), type_="check")
        batch.drop_constraint(op.f("ck_connector_connections_catalog_claim_generation_non_negative"), type_="check")
        batch.drop_constraint(op.f("ck_connector_connections_catalog_generation_non_negative"), type_="check")
        batch.drop_constraint(op.f("ck_connector_connections_current_catalog_digest_valid"), type_="check")
        batch.drop_column("catalog_claim_owner")
        batch.drop_column("catalog_available_at")
        batch.drop_column("catalog_generation")
        batch.drop_column("catalog_claim_generation")
        batch.drop_column("catalog_attempt_count")
        batch.drop_column("current_catalog_digest")
        batch.drop_column("catalog_last_error_code")
        batch.drop_column("catalog_claim_expires_at")
    with op.batch_alter_table("mcp_connections") as batch:
        batch.add_column(sa.Column("refresh_claim_generation", sa.BigInteger(), server_default="0", nullable=False))
        batch.add_column(sa.Column("refresh_claim_owner", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("refresh_claim_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("refresh_available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        )
        batch.add_column(sa.Column("refresh_last_error_code", sa.String(length=128), nullable=True))
        batch.drop_index(op.f("ix_mcp_connections_catalog_reconcile"))
        batch.create_index(
            "ix_mcp_connections_refresh_reconcile",
            ["status", "refresh_available_at", "refresh_claim_expires_at", "id"],
            unique=False,
        )
        batch.drop_constraint(op.f("ck_mcp_connections_catalog_claim_generation_non_negative"), type_="check")
        batch.drop_constraint(op.f("ck_mcp_connections_catalog_generation_non_negative"), type_="check")
        batch.drop_constraint(op.f("ck_mcp_connections_current_catalog_digest_valid"), type_="check")
        batch.create_check_constraint(
            op.f("ck_mcp_connections_refresh_claim_generation_non_negative"), "refresh_claim_generation >= 0"
        )
        batch.drop_column("catalog_claim_owner")
        batch.drop_column("catalog_available_at")
        batch.drop_column("catalog_generation")
        batch.drop_column("catalog_claim_generation")
        batch.drop_column("current_catalog_digest")
        batch.drop_column("catalog_last_error_code")
        batch.drop_column("catalog_claim_expires_at")
    with _alter_runs() as batch:
        batch.drop_constraint(op.f("ck_runs_mcp_snapshot_content_type_valid"), type_="check")
        batch.drop_constraint(op.f("ck_runs_mcp_snapshot_digest_sha256"), type_="check")
        batch.drop_constraint(op.f("ck_runs_mcp_snapshot_size_positive"), type_="check")
        batch.drop_column("mcp_tool_snapshot_content_type")
        batch.drop_column("mcp_tool_snapshot_size_bytes")
        batch.drop_column("mcp_tool_snapshot_digest_sha256")
        batch.drop_column("mcp_tool_snapshot_schema_version")


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""

    with _alter_runs() as batch:
        batch.add_column(
            sa.Column("mcp_tool_snapshot_schema_version", sa.VARCHAR(length=32), autoincrement=False, nullable=False)
        )
        batch.add_column(
            sa.Column("mcp_tool_snapshot_digest_sha256", sa.VARCHAR(length=64), autoincrement=False, nullable=False)
        )
        batch.add_column(sa.Column("mcp_tool_snapshot_size_bytes", sa.BIGINT(), autoincrement=False, nullable=False))
        batch.add_column(
            sa.Column("mcp_tool_snapshot_content_type", sa.VARCHAR(length=255), autoincrement=False, nullable=False)
        )
        batch.create_check_constraint(op.f("ck_runs_mcp_snapshot_size_positive"), "mcp_tool_snapshot_size_bytes > 0")
        batch.create_check_constraint(
            op.f("ck_runs_mcp_snapshot_digest_sha256"), "length(mcp_tool_snapshot_digest_sha256) = 64"
        )
        batch.create_check_constraint(
            op.f("ck_runs_mcp_snapshot_content_type_valid"),
            "mcp_tool_snapshot_content_type = 'application/vnd.a13n.mcp-tool-snapshot+json'",
        )
    with op.batch_alter_table("mcp_connections") as batch:
        batch.add_column(
            sa.Column(
                "catalog_claim_expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
            )
        )
        batch.add_column(
            sa.Column("catalog_last_error_code", sa.VARCHAR(length=128), autoincrement=False, nullable=True)
        )
        batch.add_column(sa.Column("current_catalog_digest", sa.VARCHAR(length=64), autoincrement=False, nullable=True))
        batch.add_column(sa.Column("catalog_claim_generation", sa.BIGINT(), autoincrement=False, nullable=False))
        batch.add_column(sa.Column("catalog_generation", sa.BIGINT(), autoincrement=False, nullable=False))
        batch.add_column(
            sa.Column("catalog_available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False)
        )
        batch.add_column(sa.Column("catalog_claim_owner", sa.VARCHAR(length=128), autoincrement=False, nullable=True))
        batch.drop_constraint(op.f("ck_mcp_connections_refresh_claim_generation_non_negative"), type_="check")
        batch.create_check_constraint(
            op.f("ck_mcp_connections_current_catalog_digest_valid"),
            "current_catalog_digest IS NULL OR length(current_catalog_digest) = 64",
        )
        batch.create_check_constraint(
            op.f("ck_mcp_connections_catalog_generation_non_negative"), "catalog_generation >= 0"
        )
        batch.create_check_constraint(
            op.f("ck_mcp_connections_catalog_claim_generation_non_negative"), "catalog_claim_generation >= 0"
        )
        batch.drop_index("ix_mcp_connections_refresh_reconcile")
        batch.create_index(
            op.f("ix_mcp_connections_catalog_reconcile"),
            ["status", "catalog_available_at", "catalog_claim_expires_at", "id"],
            unique=False,
        )
        batch.drop_column("refresh_last_error_code")
        batch.drop_column("refresh_available_at")
        batch.drop_column("refresh_claim_expires_at")
        batch.drop_column("refresh_claim_owner")
        batch.drop_column("refresh_claim_generation")
    with op.batch_alter_table("connector_connections") as batch:
        batch.add_column(
            sa.Column(
                "catalog_claim_expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
            )
        )
        batch.add_column(
            sa.Column("catalog_last_error_code", sa.VARCHAR(length=128), autoincrement=False, nullable=True)
        )
        batch.add_column(sa.Column("current_catalog_digest", sa.VARCHAR(length=64), autoincrement=False, nullable=True))
        batch.add_column(sa.Column("catalog_attempt_count", sa.BIGINT(), autoincrement=False, nullable=False))
        batch.add_column(sa.Column("catalog_claim_generation", sa.BIGINT(), autoincrement=False, nullable=False))
        batch.add_column(sa.Column("catalog_generation", sa.BIGINT(), autoincrement=False, nullable=False))
        batch.add_column(
            sa.Column("catalog_available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False)
        )
        batch.add_column(sa.Column("catalog_claim_owner", sa.VARCHAR(length=128), autoincrement=False, nullable=True))
        batch.create_check_constraint(
            op.f("ck_connector_connections_current_catalog_digest_valid"),
            "current_catalog_digest IS NULL OR length(current_catalog_digest) = 64",
        )
        batch.create_check_constraint(
            op.f("ck_connector_connections_catalog_generation_non_negative"), "catalog_generation >= 0"
        )
        batch.create_check_constraint(
            op.f("ck_connector_connections_catalog_claim_generation_non_negative"), "catalog_claim_generation >= 0"
        )
        batch.create_check_constraint(
            op.f("ck_connector_connections_catalog_attempt_count_non_negative"), "catalog_attempt_count >= 0"
        )
    op.create_table(
        "connector_tool_catalogs",
        sa.Column("id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("connector_connection_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("digest_sha256", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("object_key", sa.VARCHAR(length=1024), autoincrement=False, nullable=False),
        sa.Column("size_bytes", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("tool_count", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("connector_credential_generation", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("connection_setup_generation", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("compatibility_profile", sa.VARCHAR(length=128), autoincrement=False, nullable=False),
        sa.Column("provider_version", sa.VARCHAR(length=128), autoincrement=False, nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.Column("retain_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.CheckConstraint(
            "connection_setup_generation >= 1",
            name=op.f("ck_connector_tool_catalogs_connection_setup_generation_positive"),
        ),
        sa.CheckConstraint(
            "connector_credential_generation >= 1",
            name=op.f("ck_connector_tool_catalogs_credential_generation_positive"),
        ),
        sa.CheckConstraint("length(digest_sha256) = 64", name=op.f("ck_connector_tool_catalogs_digest_bounded")),
        sa.CheckConstraint("size_bytes >= 1", name=op.f("ck_connector_tool_catalogs_size_positive")),
        sa.CheckConstraint("tool_count >= 0", name=op.f("ck_connector_tool_catalogs_tool_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["connector_connection_id", "organization_id", "workspace_id"],
            ["connector_connections.id", "connector_connections.organization_id", "connector_connections.workspace_id"],
            name=op.f("fk_connector_tool_catalogs_connector_connection_id_conn_6dab"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_tool_catalogs")),
    )
    with op.batch_alter_table("connector_tool_catalogs") as batch:
        batch.create_index(
            op.f("uq_connector_tool_catalogs_digest"), ["connector_connection_id", "digest_sha256"], unique=True
        )
        batch.create_index(op.f("ix_connector_tool_catalogs_retention"), ["retain_until", "id"], unique=False)
        batch.create_index(
            op.f("ix_connector_tool_catalogs_latest"), ["connector_connection_id", "published_at", "id"], unique=False
        )
    op.create_table(
        "mcp_tool_catalogs",
        sa.Column("id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("mcp_connection_id", sa.VARCHAR(length=72), autoincrement=False, nullable=False),
        sa.Column("digest_sha256", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("object_key", sa.VARCHAR(length=1024), autoincrement=False, nullable=False),
        sa.Column("size_bytes", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("tool_count", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("credential_generation", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("catalog_generation", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.Column("protocol_revision", sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column("server_name", sa.VARCHAR(length=128), autoincrement=False, nullable=False),
        sa.Column("server_version", sa.VARCHAR(length=128), autoincrement=False, nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.Column("retain_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.CheckConstraint("catalog_generation >= 1", name=op.f("ck_mcp_tool_catalogs_catalog_generation_positive")),
        sa.CheckConstraint(
            "credential_generation >= 0", name=op.f("ck_mcp_tool_catalogs_credential_generation_non_negative")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_mcp_tool_catalogs_size_bytes_positive")),
        sa.CheckConstraint("tool_count >= 0", name=op.f("ck_mcp_tool_catalogs_tool_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["mcp_connection_id", "organization_id", "workspace_id"],
            ["mcp_connections.id", "mcp_connections.organization_id", "mcp_connections.workspace_id"],
            name=op.f("fk_mcp_tool_catalogs_mcp_connection_id_mcp_connections"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_tool_catalogs")),
    )
    with op.batch_alter_table("mcp_tool_catalogs") as batch:
        batch.create_index(op.f("uq_mcp_tool_catalogs_digest"), ["mcp_connection_id", "digest_sha256"], unique=True)
        batch.create_index(op.f("ix_mcp_tool_catalogs_retention"), ["retain_until", "id"], unique=False)
        batch.create_index(
            op.f("ix_mcp_tool_catalogs_latest"), ["mcp_connection_id", "published_at", "id"], unique=False
        )


@contextmanager
def _alter_runs():
    connection = op.get_bind()
    triggers = []
    if connection.dialect.name == "sqlite":
        triggers = connection.scalars(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'runs'")
        ).all()
    with op.batch_alter_table("runs") as batch:
        yield batch
    for sql in triggers:
        op.execute(sql)
