"""Durable dashboard documents with the standard live-resource version."""

from typing import ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class DashboardRow(Stamped, Base):
    __tablename__ = "metrics_dashboards"
    KIND: ClassVar[str] = "metrics_dashboard"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        Index("uq_metrics_dashboards_builtin", "workspace_id", unique=True, postgresql_where=text("builtin")),
        rules(identity_guarded("metrics_dashboards")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    builtin: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
