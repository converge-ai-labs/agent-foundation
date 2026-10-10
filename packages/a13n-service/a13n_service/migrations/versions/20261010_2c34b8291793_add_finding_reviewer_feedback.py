"""add finding reviewer feedback

Revision ID: 2c34b8291793
Revises: 574a1b928f40

Transactional DDL locks the new Findings table, adds an empty note for existing
rows and builds an ordinary revision-feedback index. No note backfill job or
new table is required. Upgrade API/Worker builds together; failed DDL rolls back
and is retryable. Prefer forward repair: downgrade discards notes and refuses
false-positive assessments that the older constraint cannot represent.
"""

import sqlalchemy as sa
from alembic import op

revision = "2c34b8291793"
down_revision = "574a1b928f40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("assessment_note", sa.String(), nullable=False, server_default=""))
    op.alter_column("findings", "assessment_note", server_default=None)
    op.drop_constraint(op.f("ck_findings_assessment"), "findings", type_="check")
    op.create_check_constraint(
        op.f("ck_findings_assessment"),
        "findings",
        "assessment IN ('unreviewed', 'confirmed', 'expected', 'insufficient', 'false_positive')",
    )
    op.create_index(
        "ix_findings_revision_feedback",
        "findings",
        ["workspace_id", "agent_id", "agent_revision_id", "updated_at", "id"],
        unique=False,
    )
    op.drop_constraint(op.f("fk_findings_workspace_id_finding_analyses"), "findings", type_="foreignkey")
    op.drop_column("findings", "analysis_id")


def downgrade() -> None:
    # Do not silently convert a disproven diagnosis into another reviewer judgment.
    op.drop_constraint(op.f("ck_findings_assessment"), "findings", type_="check")
    op.create_check_constraint(
        op.f("ck_findings_assessment"),
        "findings",
        "assessment IN ('unreviewed', 'confirmed', 'expected', 'insufficient')",
    )
    op.add_column("findings", sa.Column("analysis_id", sa.VARCHAR(), autoincrement=False, nullable=True))
    op.execute("""
        UPDATE findings AS f SET analysis_id = a.id FROM finding_analyses AS a
        WHERE f.workspace_id = a.workspace_id AND f.source_run_id = a.run_id
    """)
    op.create_foreign_key(
        op.f("fk_findings_workspace_id_finding_analyses"),
        "findings",
        "finding_analyses",
        ["workspace_id", "analysis_id"],
        ["workspace_id", "id"],
    )
    op.drop_index("ix_findings_revision_feedback", table_name="findings")
    op.drop_column("findings", "assessment_note")
