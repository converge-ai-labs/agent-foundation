"""Monitoring registrations confer no Environment or execution authority."""

from typing import ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class DeviceRow(Stamped, Base):
    __tablename__ = "monitoring_devices"
    KIND: ClassVar[str] = "monitoring_device"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "metrics_data_source_id"],
            ["metrics_data_sources.workspace_id", "metrics_data_sources.id"],
            name="fk_monitoring_devices_metrics_data_source",
        ),
        UniqueConstraint("workspace_id", "device_id", name="uq_monitoring_devices_identity"),
        rules(identity_guarded("monitoring_devices")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    device_id: Mapped[str]
    name: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)
    # Random attachment credential returned only on creation/rotation.
    token_hash: Mapped[str] = mapped_column(String(64))
    metrics_data_source_id: Mapped[str | None]
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
