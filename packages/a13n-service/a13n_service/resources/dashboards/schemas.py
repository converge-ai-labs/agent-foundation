"""Editable monitoring documents; saved queries never confer execution authority."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.providers.metrics import Expression
from a13n_service.providers.metrics_catalog import Metric

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
PanelId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0, le=1000)
    w: int = Field(ge=3, le=12)
    h: int = Field(ge=3, le=16)

    @model_validator(mode="after")
    def fits(self) -> Self:
        if self.x + self.w > 12:
            raise ValueError("Panel must fit within the twelve-column grid")
        return self


class TypedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["metric"] = "metric"
    metric: Metric
    history: Literal["configured", "temporary"] = "configured"


class PromQLQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["promql"] = "promql"
    source_id: str = Field(pattern=r"^mds_[a-zA-Z0-9_-]+$", max_length=72)
    expression: Expression


type PanelQuery = Annotated[TypedQuery | PromQLQuery, Field(discriminator="kind")]


class Panel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: PanelId
    title: Name
    description: str = Field(default="", max_length=2048)
    position: Position
    visualization: Literal["timeseries", "stat"] = "timeseries"
    unit: Literal["auto", "ratio", "bytes", "bytes_per_second", "operations_per_second", "count", "seconds"] = "auto"
    legend: bool = True
    queries: list[PanelQuery] = Field(min_length=1, max_length=4)


class DashboardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    range_seconds: int = Field(default=3600, ge=60, le=7776000)
    refresh_seconds: Literal[0, 10, 30, 60, 300] = 30
    panels: list[Panel] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def unique_panels(self) -> Self:
        if len({panel.id for panel in self.panels}) != len(self.panels):
            raise ValueError("Panel IDs must be unique within a dashboard")
        return self


class DashboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    config: DashboardConfig


class DashboardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    config: DashboardConfig | None = None


class Dashboard(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    name: str
    config: DashboardConfig
    builtin: bool
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class DashboardPage(BaseModel):
    items: list[Dashboard]
    next_cursor: str | None


def default_config() -> DashboardConfig:
    """The default and user panels share one schema and renderer."""
    groups: list[tuple[str, list[Metric]]] = [
        ("CPU", ["system_cpu_busy_ratio"]),
        ("Memory", ["system_memory_used_bytes", "system_memory_available_bytes"]),
        ("Network receive", ["network_received_bytes_per_second"]),
        ("Network transmit", ["network_transmitted_bytes_per_second"]),
        ("Disk throughput", ["disk_read_bytes_per_second", "disk_written_bytes_per_second"]),
        ("Disk operations", ["disk_read_operations_per_second", "disk_write_operations_per_second"]),
        ("Cgroup memory", ["cgroup_memory_current_bytes"]),
        ("Processes", ["system_processes", "system_threads", "cgroup_pids_current"]),
        ("Connections", ["tcp_connections", "udp_sockets"]),
        ("Daemon", ["daemon_sessions", "daemon_processes", "daemon_connections", "daemon_transfers"]),
    ]
    return DashboardConfig(
        panels=[
            Panel(
                id=f"panel-{index + 1}",
                title=title,
                position=Position(x=(index % 2) * 6, y=(index // 2) * 5, w=6, h=5),
                queries=[TypedQuery(metric=metric) for metric in metrics],
            )
            for index, (title, metrics) in enumerate(groups)
        ]
    )
