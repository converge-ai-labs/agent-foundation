"""Models belong to one workspace and are served by one of its model providers."""

from typing import ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class ModelRow(Stamped, Base):
    __tablename__ = "models"
    KIND: ClassVar[str] = "model"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "provider_id"], ["model_providers.workspace_id", "model_providers.id"]),
        rules(identity_guarded("models")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    provider_id: Mapped[str]
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(server_default="")
    config: Mapped[dict] = mapped_column(JSONB)
    pricing: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    catalog_ref: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
