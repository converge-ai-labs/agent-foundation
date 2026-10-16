"""Skill heads and their immutable revisions, each naming one validated package."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules
from a13n_service.resources.revisions import RevisionColumns


class SkillRow(Stamped, Base):
    __tablename__ = "skills"
    KIND: ClassVar[str] = "skill"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["id", "default_revision_id"],
            ["skill_revisions.skill_id", "skill_revisions.id"],
            name="fk_skills_default_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        rules(identity_guarded("skills")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    default_revision_id: Mapped[str | None] = mapped_column(String(72))
    labels: Mapped[dict] = mapped_column(JSONB)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

    @property
    def builtin(self) -> bool:
        # Every skill is the workspace's own.
        return False


class SkillRevisionRow(RevisionColumns):
    __tablename__ = "skill_revisions"
    KIND = "skill_revision"
    HEAD_TABLE = "skills"
    HEAD_KEY = "skill_id"
    ID_PREFIX = "skr"
    skill_id: Mapped[str]
    # The staged upload holding the package archive, referenced in place.
    package_ref: Mapped[str]
