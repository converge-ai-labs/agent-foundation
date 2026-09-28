"""Environment templates are live rows without revisions; an instance keeps the recipe of its first dispatch."""

from typing import ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class EnvironmentTemplateRow(Stamped, Base):
    __tablename__ = "environment_templates"
    KIND: ClassVar[str] = "environment_template"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "provider_id"], ["environment_providers.workspace_id", "environment_providers.id"]
        ),
        rules(identity_guarded("environment_templates")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str | None]
    provider_id: Mapped[str]
    # Base image, resources, network policy, idle policy and storage semantics, validated by the provider type.
    config: Mapped[dict] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    labels: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
