"""Workspace findings and bounded analysis provenance; execution remains owned by ordinary runs."""

from typing import ClassVar

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class AnalysisRow(Stamped, Base):
    __tablename__ = "finding_analyses"
    KIND: ClassVar[str] = "finding_analysis"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "created_by_id", "request_key"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
        Index("ix_finding_analyses_workspace_created", "workspace_id", "created_at", "id"),
        rules(identity_guarded("finding_analyses")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    # Omission selects workspace traces rather than one target Agent.
    agent_id: Mapped[str | None]
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    request_key: Mapped[str] = mapped_column(String(512))
    request_digest: Mapped[str]
    selection: Mapped[dict] = mapped_column(JSONB)
    selected_traces: Mapped[list] = mapped_column(JSONB)
    selection_truncated: Mapped[bool] = mapped_column(Boolean)
    read_trace_ids: Mapped[list] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class FindingRow(Stamped, Base):
    __tablename__ = "findings"
    KIND: ClassVar[str] = "finding"
    __table_args__ = (
        UniqueConstraint("workspace_id", "created_by_id", "source_key"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
        ForeignKeyConstraint(["agent_id", "agent_revision_id"], ["agent_revisions.agent_id", "agent_revisions.id"]),
        CheckConstraint(
            "category IN ('unclear_request','instruction_issue','tool_design','tool_usage','tool_execution','answer_quality','context_gap','workflow_issue','boundary_violation')",
            name="category",
        ),
        CheckConstraint("severity IN ('critical', 'warning', 'suggestion')", name="severity"),
        CheckConstraint(
            "assessment IN ('unreviewed', 'confirmed', 'expected', 'insufficient', 'false_positive')", name="assessment"
        ),
        Index("ix_findings_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_findings_revision_feedback", "workspace_id", "agent_id", "agent_revision_id", "updated_at", "id"),
        rules(identity_guarded("findings")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    agent_id: Mapped[str]
    agent_revision_id: Mapped[str]
    title: Mapped[str]
    category: Mapped[str]
    severity: Mapped[str]
    explanation: Mapped[str]
    suggestion: Mapped[str]
    evidence: Mapped[list] = mapped_column(JSONB)
    limitations: Mapped[str]
    source_key: Mapped[str]
    source_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    assessment: Mapped[str]
    assessment_note: Mapped[str]
    closed: Mapped[bool] = mapped_column(Boolean)
