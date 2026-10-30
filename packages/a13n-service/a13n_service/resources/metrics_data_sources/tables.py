"""Live workspace metrics configuration with separately sealed read and write credentials."""

from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class DataSourceRow(Stamped, Base):
    __tablename__ = "metrics_data_sources"
    KIND: ClassVar[str] = "metrics_data_source"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        Index("uq_metrics_data_sources_builtin", "workspace_id", unique=True, postgresql_where=text("builtin")),
        CheckConstraint("config->>'collection' IN ('native', 'pull', 'proxy', 'external')", name="collection"),
        CheckConstraint(
            'NOT builtin OR (config = \'{"collection": "proxy", "export": null, "query": null, "collector": null}\'::jsonb'
            " AND export_credentials IS NULL AND query_credentials IS NULL AND enabled)",
            name="builtin",
        ),
        rules(identity_guarded("metrics_data_sources")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    builtin: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    export_credentials: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    query_credentials: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

    @property
    def has_export_credentials(self) -> bool:
        return self.export_credentials is not None

    @property
    def has_query_credentials(self) -> bool:
        return self.query_credentials is not None
