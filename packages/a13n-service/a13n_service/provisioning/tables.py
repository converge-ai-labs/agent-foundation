"""Completion facts survive user edits and deletion of the initially created resources."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, immutable, rules


class WorkspaceProvisioningRow(Base):
    __tablename__ = "workspace_provisioning"
    __table_args__ = (
        CheckConstraint("component IN ('local', 'docker')", name="component"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        rules(immutable("workspace_provisioning")),
    )
    organization_id: Mapped[str]
    workspace_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    component: Mapped[str] = mapped_column(primary_key=True)
    # Historical identifiers, deliberately without resource FKs: users may delete the defaults.
    provider_id: Mapped[str] = mapped_column(String(72))
    template_id: Mapped[str] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
