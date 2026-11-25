"""An asset is immutable upload content; retirement stops new use and keeps the content readable."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class AssetRow(Stamped, Base):
    __tablename__ = "assets"
    KIND: ClassVar[str] = "asset"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        UniqueConstraint("workspace_id", "id"),
        Index("ix_assets_workspace_created", "workspace_id", "created_at", "id"),
        # One asset per staged upload: creating it again from the same upload reads the existing one back.
        UniqueConstraint("workspace_id", "content_ref"),
        CheckConstraint("size >= 0", name="size"),
        rules(identity_guarded("assets")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    content_type: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    digest: Mapped[str]
    content_ref: Mapped[str]
    source: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
