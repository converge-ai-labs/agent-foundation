"""Bounded process-local chart history. This is not an export queue or durable record."""

from array import array
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from typing import Literal

from a13n_envd_client.eip.v1 import (
    DeviceMetricsResult,
    DiskIoMetrics,
    MetricsSample,
    NetworkInterfaceMetrics,
    NetworkMetrics,
    SystemInfo,
)
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.providers.metrics_catalog import RATE_FIELDS, SCALARS, MetricQuery, scalars

RETENTION_MINUTES = 1440
RESOURCE_MINUTES = 60
MAX_RATE_SOURCES = 64  # Per resource kind, including retained disappeared sources.
GAUGES = SCALARS[:3]
# timestamp, contiguous segment, sample-weighted average, peak.
Sample = tuple[int, int, float, float]


class Series(BaseModel):
    model_config = ConfigDict(frozen=True)
    metric: str
    labels: dict[str, str] = Field(default_factory=dict)
    points: list[Sample]


class History(BaseModel):
    source: Literal["temporary"] = "temporary"
    temporary: bool = True
    retention_seconds: int
    resolution_seconds: int
    available_from: int | None
    available_until: int | None
    series: list[Series]
    omitted_sources: int = 0
    inventory_truncated: bool = False


class MinuteHistory:
    """Fixed numeric ring; resource rings are allocated on first observation."""

    def __init__(self, names: tuple[str, ...] = GAUGES, minutes: int = RETENTION_MINUTES) -> None:
        self.names, self.minutes = names, minutes
        self.columns = 2 + 3 * len(names)
        self.values = array("d", [0.0]) * (minutes * self.columns)
        self.newest = -1

    def add(self, timestamp: float, segment: int, values: tuple[float | int | None, ...]) -> None:
        minute = int(timestamp // 60)
        if minute < self.newest:
            return  # A Host clock correction must not overwrite a later observation.
        self.newest = minute
        offset = minute % self.minutes * self.columns
        if self.values[offset] != minute or self.values[offset + 1] != segment:
            self.values[offset : offset + self.columns] = array(
                "d", [float(minute), float(segment)] + [0.0] * (self.columns - 2)
            )
        for index, value in enumerate(values):
            if value is None:
                continue
            position = offset + 2 + index * 3
            count = self.values[position + 1]
            self.values[position] += value
            self.values[position + 1] = count + 1
            self.values[position + 2] = max(self.values[position + 2], value) if count else value

    def read(self, *, now: float, start: float, end: float, metric: str | None = None, step: float = 60) -> History:
        series = []
        available_until: int | None = None
        width = max(1, ceil(step / 60))
        first = max(int(now // 60) - self.minutes + 1, ceil(start / 60))
        last = min(int(end // 60), int(now // 60), self.newest)
        for index, name in enumerate(self.names):
            if metric is not None and name != metric:
                continue
            points: list[Sample] = []
            total = count = peak = 0.0
            bucket = segment = previous_segment = 0
            previous_minute = -2
            timestamp = 0
            for minute in range(first, last + 1):
                offset = minute % self.minutes * self.columns
                position = offset + 2 + index * 3
                if self.values[offset] != minute or not self.values[position + 1]:
                    continue
                available_until = max(available_until or 0, minute * 60)
                raw_segment = int(self.values[offset + 1])
                contiguous = minute == previous_minute + 1 and raw_segment == previous_segment
                if count and (minute // width != bucket or not contiguous):
                    points.append((timestamp, segment, total / count, peak))
                    total = count = peak = 0.0
                if not contiguous:
                    # A query-local segment also splits missing values inside a coarse bucket.
                    segment = minute + 1
                if not count:
                    timestamp, bucket = minute * 60, minute // width
                total += self.values[position]
                count += self.values[position + 1]
                peak = max(peak, self.values[position + 2])
                previous_minute, previous_segment = minute, raw_segment
            if count:
                points.append((timestamp, segment, total / count, peak))
            if points:
                series.append(Series(metric=name, points=points))
        return history(series, retention=self.minutes * 60, resolution=width * 60, available_until=available_until)


def history(
    series: list[Series],
    *,
    retention: int,
    resolution: int,
    available_until: int | None = None,
    omitted: int = 0,
    truncated: bool = False,
) -> History:
    return History(
        retention_seconds=retention,
        resolution_seconds=resolution,
        available_from=min((s.points[0][0] for s in series), default=None),
        available_until=available_until,
        series=series,
        omitted_sources=omitted,
        inventory_truncated=truncated,
    )


type Status = Literal["waiting", "ready", "stale", "offline", "disabled", "unsupported", "not_ready", "capacity"]


class Observation(BaseModel):
    status: Status
    device_id: str
    generation: int | None = None
    system_info: SystemInfo | None = None
    sample: MetricsSample | None = None
    sample_interval_ms: int | None = None
    age_ms: int | None = None
    received_at: datetime | None = None
    last_sample_at: datetime | None = None


@dataclass(slots=True)
class Resource:
    identity: str
    ring: MinuteHistory
    previous: tuple[int | None, ...] | None = None
    observed_at: float = 0
    segment: int = 1

    def invalidate(self) -> None:
        if self.previous is not None:
            self.previous = None
            self.segment += 1

    def add(self, values: tuple[int | None, ...], *, timestamp: float, monotonic: float, max_gap: float) -> None:
        elapsed = monotonic - self.observed_at
        if self.previous is not None and (
            elapsed <= 0
            or elapsed > max_gap
            or any(
                before is not None and (value is None or value < before)
                for before, value in zip(self.previous, values, strict=True)
            )
        ):
            self.invalidate()
        rates = tuple(
            None if before is None or value is None else (value - before) / elapsed
            for before, value in zip(self.previous or (None,) * len(values), values, strict=True)
        )
        self.ring.add(timestamp, self.segment, rates)
        self.previous, self.observed_at = values, monotonic


@dataclass(slots=True)
class Cache:
    device_id: str
    history: MinuteHistory = field(default_factory=lambda: MinuteHistory(SCALARS))
    resources: dict[tuple[str, str], Resource] = field(default_factory=dict)
    omitted: dict[str, int] = field(default_factory=dict)
    truncated: dict[str, bool] = field(default_factory=dict)
    system_info: SystemInfo | None = None
    generation: int | None = None
    sequence: int = 0
    segment: int = 0
    status: Status = "waiting"
    report: DeviceMetricsResult | None = None
    received_at: float | None = None
    received_monotonic: float = 0
    last_sample_at: float | None = None

    def invalidate_rates(self) -> None:
        for resource in self.resources.values():
            resource.invalidate()

    def accept(self, report: DeviceMetricsResult, *, now: float, monotonic: float, poll_seconds: float = 10) -> bool:
        """Return whether a fresh distinct sample is eligible for export as well as charts."""
        if report.device_id != self.device_id:
            raise ValueError("Device identity changed")
        if report.generation != self.generation:
            self.generation = report.generation
            self.sequence = 0
            self.segment += 1
            self.last_sample_at = None
            self.resources.clear()
        self.report, self.received_at, self.received_monotonic = report, now, monotonic
        sample = report.sample
        self.status = "waiting" if sample is None else "ready"
        if sample is None:
            self.invalidate_rates()
            return False
        if sample.sample_age_ms > 2 * report.sample_interval_ms:
            self.status = "stale"
            self.invalidate_rates()
            return False
        if sample.sequence <= self.sequence:
            return False
        self.sequence = sample.sequence
        self.last_sample_at = now - sample.sample_age_ms / 1000
        self.history.add(self.last_sample_at, self.segment, scalars(sample))
        observed_at = monotonic - sample.sample_age_ms / 1000
        max_gap = max(30, 3 * poll_seconds, 3 * report.sample_interval_ms / 1000)
        for kind, inventory in (
            ("network", sample.network),
            ("disk", sample.disks),
            ("cgroup_io", None if sample.cgroup_v2 is None else sample.cgroup_v2.io),
        ):
            devices: Mapping[str, NetworkInterfaceMetrics | DiskIoMetrics]
            if inventory is None:
                devices = {}
            elif isinstance(inventory, NetworkMetrics):
                devices = inventory.interfaces
            else:
                devices = inventory.devices
            self.omitted[kind] = max(0, len(devices) - MAX_RATE_SOURCES)
            self.truncated[kind] = inventory is not None and inventory.truncated
            for key, resource in list(self.resources.items()):
                if key[0] == kind and key[1] not in devices:
                    resource.invalidate()
                    if observed_at - resource.observed_at > RESOURCE_MINUTES * 60:
                        del self.resources[key]
            selected = sorted(devices, key=lambda name: ((kind, name) not in self.resources, name))[:MAX_RATE_SOURCES]
            for name in selected:
                device = devices[name]
                key = kind, name
                resource = self.resources.get(key)
                if resource is None or resource.identity != device.identity:
                    if resource is None and sum(k[0] == kind for k in self.resources) >= MAX_RATE_SOURCES:
                        evicted = min(
                            (k for k in self.resources if k[0] == kind and k[1] not in selected),
                            key=lambda k: self.resources[k].observed_at,
                        )
                        del self.resources[evicted]
                    resource = Resource(
                        device.identity,
                        MinuteHistory(
                            tuple(f"{kind}_{name}_per_second" for name in RATE_FIELDS[kind]), RESOURCE_MINUTES
                        ),
                    )
                    self.resources[key] = resource
                values = (
                    (device.received_bytes, device.transmitted_bytes)
                    if isinstance(device, NetworkInterfaceMetrics)
                    else (device.read_bytes, device.written_bytes, device.read_operations, device.write_operations)
                )
                resource.add(values, timestamp=self.last_sample_at, monotonic=observed_at, max_gap=max_gap)
        return True

    def read(self, query: MetricQuery, *, now: float) -> History:
        if query.metric in SCALARS:
            return self.history.read(now=now, start=query.start, end=query.end, step=query.step, metric=query.metric)
        kind = next(kind for kind in RATE_FIELDS if query.metric.startswith(kind + "_"))
        series = []
        coverage: list[int] = []
        for (resource_kind, name), resource in sorted(self.resources.items()):
            if resource_kind != kind:
                continue
            result = resource.ring.read(now=now, start=query.start, end=query.end, step=query.step, metric=query.metric)
            if result.available_until is not None:
                coverage.append(result.available_until)
            series.extend(
                item.model_copy(
                    update={
                        "labels": {"interface" if kind == "network" else "disk": name, "identity": resource.identity}
                    }
                )
                for item in result.series
            )
        return history(
            series,
            retention=RESOURCE_MINUTES * 60,
            available_until=max(coverage, default=None),
            resolution=max(1, ceil(query.step / 60)) * 60,
            omitted=self.omitted.get(kind, 0),
            truncated=self.truncated.get(kind, False),
        )

    def observation(self, *, monotonic: float, include_snapshot: bool = True) -> Observation:
        report = self.report
        sample = None if report is None else report.sample
        status, age = self.status, None
        if sample is not None and report is not None:
            age = int(sample.sample_age_ms + max(0, monotonic - self.received_monotonic) * 1000)
            if status == "ready" and age > 2 * report.sample_interval_ms:
                status = "stale"
            if include_snapshot:
                sample = sample.model_copy(update={"sample_age_ms": age})
        return Observation(
            status=status,
            device_id=self.device_id,
            generation=self.generation,
            system_info=self.system_info if include_snapshot else None,
            sample=sample if include_snapshot else None,
            sample_interval_ms=None if report is None else report.sample_interval_ms,
            age_ms=age,
            received_at=None if self.received_at is None else datetime.fromtimestamp(self.received_at, UTC),
            last_sample_at=None if self.last_sample_at is None else datetime.fromtimestamp(self.last_sample_at, UTC),
        )
