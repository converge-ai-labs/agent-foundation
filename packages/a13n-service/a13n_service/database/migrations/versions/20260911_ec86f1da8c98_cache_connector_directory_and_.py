"""cache connector directory and coordinate shared setup.

Revision ID: ec86f1da8c98
Revises: 2999349e6c69
Create Date: 2026-09-11 07:55:44.038060+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ec86f1da8c98"
down_revision: str | Sequence[str] | None = "2999349e6c69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the schema change."""
    op.create_table(
        "connector_shared_setup_claims",
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("provider_id", sa.String(length=72), nullable=False),
        sa.Column("connector_key", sa.String(length=128), nullable=False),
        sa.Column("configuration_key", sa.String(length=128), nullable=False),
        sa.Column("credential_generation", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "credential_generation >= 1",
            name=op.f("ck_connector_shared_setup_claims_credential_generation_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["connector_providers.id"],
            name=op.f("fk_connector_shared_setup_claims_provider_id_connector_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_shared_setup_claims")),
        sa.UniqueConstraint(
            "provider_id", "connector_key", "configuration_key", name="uq_connector_shared_setup_claims_scope"
        ),
    )
    op.add_column("connector_providers", sa.Column("directory_json", sa.JSON(), nullable=True))
    op.add_column("connector_providers", sa.Column("directory_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Reverse the schema change when it is safe to do so."""
    op.drop_column("connector_providers", "directory_updated_at")
    op.drop_column("connector_providers", "directory_json")
    op.drop_table("connector_shared_setup_claims")
