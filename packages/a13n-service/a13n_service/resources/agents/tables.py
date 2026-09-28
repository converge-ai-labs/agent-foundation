"""Agent identity and immutable, tenant-owned revisions."""

from datetime import datetime
from typing import ClassVar, Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules
from a13n_service.resources.revisions import RevisionColumns

# `builtin` is the workspace's Agent Composer, which the deployment defines.
type AgentSource = Literal["custom", "builtin"]


class AgentRow(Stamped, Base):
    __tablename__ = "agents"
    KIND: ClassVar[str] = "agent"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["id", "default_revision_id"],
            ["agent_revisions.agent_id", "agent_revisions.id"],
            name="fk_agents_default_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("source IN ('custom', 'builtin')", name="source"),
        # The Agent Composer is the workspace's one builtin agent.
        Index("uq_agents_builtin", "workspace_id", unique=True, postgresql_where=text("source = 'builtin'")),
        rules(identity_guarded("agents")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    default_revision_id: Mapped[str | None] = mapped_column(String(72))
    labels: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[AgentSource] = mapped_column(String, default="custom")
    # The avatar's object reference (`infra/images.py`).
    image: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

    @property
    def builtin(self) -> bool:
        return self.source == "builtin"


class AgentRevisionRow(RevisionColumns):
    __tablename__ = "agent_revisions"
    KIND = "agent_revision"
    HEAD_TABLE = "agents"
    HEAD_KEY = "agent_id"
    ID_PREFIX = "apr"
    agent_id: Mapped[str]
