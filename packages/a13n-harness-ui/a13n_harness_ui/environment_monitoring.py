"""Human-facing Device observations with bounded, process-local history."""

from __future__ import annotations

import time
from array import array
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import anyio
from a13n_envd_client import EIPDeviceConnection
from a13n_envd_client.eip.v1 import DiskIoMetrics, MetricsSample, NetworkInterfaceMetrics, SystemInfo
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import HarnessUiError

MAX_TARGETS = 64
HISTORY_MINUTES = 60
POLL_SECONDS = 60
COLLECTION_CONCURRENCY = 4
COLLECTION_TIMEOUT = 5
MAX_OBSERVATION_BYTES = 128 * 1024
MAX_RATE_SOURCES = 64  # Match the native per-kind inventory bound; history retains one hour.
_RATE_FIELDS = {
    "network": ("received_bytes", "transmitted_bytes"),
    "disk": ("read_bytes", "written_bytes", "read_operations", "write_operations"),
}
_LOGGER = get_logger(__name__)

Status = Literal["waiting", "not_started", "disabled", "ready", "stale", "offline", "revoked"]


class MonitoringModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Gauge(MonitoringModel):
    average: float
    peak: float


class HistoryPoint(MonitoringModel):
    minute: int
    segment: int
    cpu_busy_percent: Gauge | None = None
    memory_used_bytes: Gauge | None = None
    cgroup_memory_bytes: Gauge | None = None
    tcp_connections: Gauge | None = None
    udp_sockets: Gauge | None = None


class EnvironmentObservation(MonitoringModel):
    id: str
    name: str
    transport: Literal["http", "websocket", "stdio"]
    roots: tuple[str, ...] = ()
    status: Status
    system_info: SystemInfo | None = None
    sample: MetricsSample | None = None
    sample_interval_ms: int | None = None
    collection_interval_seconds: int = POLL_SECONDS
    age_ms: int | None = None


class MonitoringCatalog(MonitoringModel):
    environments: tuple[EnvironmentObservation, ...]
    omitted: int
    capacity: int = MAX_TARGETS
    retention_minutes: int = HISTORY_MINUTES


class RatePoint(MonitoringModel):
    minute: int
    segment: int
    rates: dict[str, Gauge]


class ResourceHistory(MonitoringModel):
    kind: Literal["network", "disk"]
    name: str
    identity: str
    history: tuple[RatePoint, ...]


class MonitoringDetail(MonitoringModel):
    environment: EnvironmentObservation
    history: tuple[HistoryPoint, ...]
    resources: tuple[ResourceHistory, ...] = ()
    omitted_rate_sources: int = 0
    retention_minutes: int = HISTORY_MINUTES


@dataclass(frozen=True, slots=True)
class MonitoringSource:
    id: str
    name: str
    transport: Literal["http", "websocket", "stdio"]
    revision: str
    acquire: Callable[[], Awaitable[EIPDeviceConnection | None]]
    roots: tuple[str, ...] = ()
    revoked: bool = False


class MinuteHistory:
    """Fixed numeric ring: minute, segment, then (sum, count, peak) per gauge."""

    def __init__(self, gauge_count: int = 5) -> None:
        self.gauge_count = gauge_count
        self.columns = 2 + 3 * gauge_count
        self.values = array("d", [0.0]) * (HISTORY_MINUTES * self.columns)

    def add(self, minute: int, segment: int, gauges: tuple[float | None, ...]) -> None:
        offset = minute % HISTORY_MINUTES * self.columns
        values = self.values
        if values[offset] != minute or values[offset + 1] != segment:
            values[offset : offset + self.columns] = array(
                "d", [float(minute), float(segment)] + [0.0] * (self.columns - 2)
            )
        for index, value in enumerate(gauges):
            if value is None:
                continue
            position = offset + 2 + index * 3
            values[position] += value
            values[position + 1] += 1
            values[position + 2] = max(values[position + 2], value)

    def rows(self, now: float) -> Iterable[tuple[int, int, tuple[Gauge | None, ...]]]:
        minute = int(now // 60)
        for timestamp in range(minute - HISTORY_MINUTES + 1, minute + 1):
            offset = timestamp % HISTORY_MINUTES * self.columns
            if self.values[offset] != timestamp or self.values[offset + 1] == 0:
                continue
            gauges: list[Gauge | None] = []
            for index in range(self.gauge_count):
                position = offset + 2 + index * 3
                total, count, peak = self.values[position : position + 3]
                gauges.append(Gauge(average=total / count, peak=peak) if count else None)
            yield timestamp, int(self.values[offset + 1]), tuple(gauges)

    def points(self, now: float) -> tuple[HistoryPoint, ...]:
        return tuple(
            HistoryPoint(
                minute=minute,
                segment=segment,
                cpu_busy_percent=gauges[0],
                memory_used_bytes=gauges[1],
                cgroup_memory_bytes=gauges[2],
                tcp_connections=gauges[3],
                udp_sockets=gauges[4],
            )
            for minute, segment, gauges in self.rows(now)
        )


@dataclass(slots=True)
class _ResourceRates:
    kind: Literal["network", "disk"]
    name: str
    identity: str
    ring: MinuteHistory
    previous: tuple[int | None, ...] | None = None
    observed_at: float = 0
    segment: int = 1

    def invalidate(self) -> None:
        self.previous = None
        self.segment += 1

    def add(self, counters: tuple[int | None, ...], observed_at: float, minute: int, max_gap: float) -> None:
        elapsed = observed_at - self.observed_at
        previous = self.previous
        if previous is not None and (
            elapsed <= 0
            or elapsed > max_gap
            or any(
                before is not None and after is not None and after < before
                for before, after in zip(previous, counters, strict=True)
            )
        ):
            self.invalidate()
            previous = None
        rates = tuple(
            None if before is None or value is None else (value - before) / elapsed
            for before, value in zip(previous or (None,) * len(counters), counters, strict=True)
        )
        self.ring.add(minute, self.segment, rates)
        self.previous = counters
        self.observed_at = observed_at

    def view(self, now: float) -> ResourceHistory:
        fields = _RATE_FIELDS[self.kind]
        return ResourceHistory(
            kind=self.kind,
            name=self.name,
            identity=self.identity,
            history=tuple(
                RatePoint(
                    minute=minute,
                    segment=segment,
                    rates={
                        f"{name}_per_second": value
                        for name, value in zip(fields, gauges, strict=True)
                        if value is not None
                    },
                )
                for minute, segment, gauges in self.ring.rows(now)
            ),
        )


@dataclass(slots=True)
class _Observation:
    source: MonitoringSource
    poll_seconds: int = POLL_SECONDS
    status: Status = "waiting"
    system_info: SystemInfo | None = None
    sample: MetricsSample | None = None
    interval_ms: int | None = None
    received_at: float = 0
    identity: tuple[str, int] | None = None
    sequence: int = 0
    segment: int = 0
    history: MinuteHistory = field(default_factory=MinuteHistory)
    resources: dict[tuple[str, str], _ResourceRates] = field(default_factory=dict)
    omitted_rate_sources: int = 0

    def invalidate_rates(self) -> None:
        for resource in self.resources.values():
            resource.invalidate()

    def add_rates(self, sample: MetricsSample, minute: int) -> None:
        observed_at = self.received_at - sample.sample_age_ms / 1000
        max_gap = max(30, 3 * self.poll_seconds, 3 * (self.interval_ms or 10000) / 1000)
        self.omitted_rate_sources = 0
        sources: tuple[
            tuple[Literal["network", "disk"], Mapping[str, NetworkInterfaceMetrics | DiskIoMetrics]], ...
        ] = (
            ("network", {} if sample.network is None else sample.network.interfaces),
            ("disk", {} if sample.disks is None else sample.disks.devices),
        )
        for kind, devices in sources:
            self.omitted_rate_sources += max(0, len(devices) - MAX_RATE_SOURCES)
            for key, resource in self.resources.items():
                if key[0] == kind and key[1] not in devices:
                    resource.invalidate()
            # Keep current sources stable; replace absent slots before accepting new sources.
            selected = sorted(devices, key=lambda name: ((kind, name) not in self.resources, name))[:MAX_RATE_SOURCES]
            for name in selected:
                device = devices[name]
                key = (kind, name)
                resource = self.resources.get(key)
                if resource is None or resource.identity != device.identity:
                    if resource is None and sum(k[0] == kind for k in self.resources) >= MAX_RATE_SOURCES:
                        evicted = min(
                            (k for k in self.resources if k[0] == kind and k[1] not in selected),
                            key=lambda k: self.resources[k].observed_at,
                        )
                        del self.resources[evicted]
                    resource = _ResourceRates(
                        kind=kind, name=name, identity=device.identity, ring=MinuteHistory(len(_RATE_FIELDS[kind]))
                    )
                    self.resources[key] = resource
                counters: tuple[int | None, ...]
                if isinstance(device, NetworkInterfaceMetrics):
                    counters = (device.received_bytes, device.transmitted_bytes)
                else:
                    counters = (
                        device.read_bytes,
                        device.written_bytes,
                        device.read_operations,
                        device.write_operations,
                    )
                resource.add(counters, observed_at, minute, max_gap)

    def view(self, now: float) -> EnvironmentObservation:
        age = None if self.sample is None else self.sample.sample_age_ms + max(0, int((now - self.received_at) * 1000))
        status = self.status
        if (
            status == "ready"
            and age is not None
            and age > max(30_000, 3 * self.poll_seconds * 1000, 3 * (self.interval_ms or 10_000))
        ):
            status = "stale"
        return EnvironmentObservation(
            id=self.source.id,
            name=self.source.name,
            transport=self.source.transport,
            roots=self.source.roots,
            status=status,
            system_info=self.system_info,
            sample=self.sample,
            sample_interval_ms=self.interval_ms,
            collection_interval_seconds=self.poll_seconds,
            age_ms=age,
        )


class EnvironmentMonitoring:
    """One App-owned collector; queries never perform remote I/O or start a daemon."""

    def __init__(self, sources: Callable[[], Iterable[MonitoringSource]], *, poll_seconds: int = POLL_SECONDS) -> None:
        self._sources = sources
        self._poll_seconds = poll_seconds
        self._observations: dict[str, _Observation] = {}
        self._omitted = 0

    def synchronize(self) -> None:
        retained: dict[str, _Observation] = {}
        omitted = 0
        for source in self._sources():
            if source.id in retained:
                continue
            if len(retained) >= MAX_TARGETS:
                omitted += 1
                continue
            previous = self._observations.get(source.id)
            if (
                previous is None
                or previous.source.revision != source.revision
                or previous.source.revoked != source.revoked
            ):
                previous = _Observation(
                    source=source,
                    poll_seconds=self._poll_seconds,
                    status="revoked" if source.revoked else "waiting",
                )
            previous.source = source
            retained[source.id] = previous
        self._observations = retained
        self._omitted = omitted

    async def catalog(self) -> MonitoringCatalog:
        self.synchronize()
        now = time.monotonic()
        return MonitoringCatalog(
            environments=tuple(item.view(now) for item in self._observations.values()), omitted=self._omitted
        )

    async def detail(self, target_id: str) -> MonitoringDetail:
        self.synchronize()
        item = self._observations.get(target_id)
        if item is None:
            raise HarnessUiError(
                "The monitored Environment is unavailable or outside the monitoring capacity.",
                code="environment_monitor_missing",
            )
        now = time.time()
        return MonitoringDetail(
            environment=item.view(time.monotonic()),
            history=item.history.points(now),
            resources=tuple(resource.view(now) for resource in item.resources.values()),
            omitted_rate_sources=item.omitted_rate_sources,
        )

    async def run(self) -> None:
        while True:
            try:
                await self.collect()
            except Exception:
                # A failed collection cycle must not terminate the App's other background work.
                _LOGGER.warning("Environment monitoring collection failed")
            await anyio.sleep(self._poll_seconds)

    async def collect(self) -> None:
        self.synchronize()
        pending = deque(self._observations.values())

        async def worker() -> None:
            while pending:
                await self._collect_one(pending.popleft())

        async with anyio.create_task_group() as group:
            for _ in range(min(COLLECTION_CONCURRENCY, len(pending))):
                group.start_soon(worker)

    async def _collect_one(self, item: _Observation) -> None:
        if item.source.revoked or self._observations.get(item.source.id) is not item:
            return
        try:
            with anyio.fail_after(COLLECTION_TIMEOUT):
                device = await item.source.acquire()
                if device is None:
                    item.status = "not_started"
                    item.invalidate_rates()
                    return
                # HTTP reconnects can reach a new daemon at the same address. describe()
                # fences a changed generation so the shared runtime can reacquire next round.
                descriptor = await device.describe() if item.source.transport == "http" else device.descriptor
                info = descriptor.system_info
                if info is not None and len(info.model_dump_json().encode()) > MAX_OBSERVATION_BYTES:
                    raise ValueError("System information exceeds the observation limit")
                # Descriptor capabilities are fixed for this connection's generation.
                if "device.metrics" not in descriptor.available_methods:
                    item.system_info = info
                    item.sample = None
                    item.interval_ms = None
                    item.status = "disabled"
                    item.invalidate_rates()
                    return
                result = await device.metrics()
                sample = result.sample
                if sample is not None and (
                    len(sample.filesystems) > 16 or len(sample.model_dump_json().encode()) > MAX_OBSERVATION_BYTES
                ):
                    raise ValueError("Metrics exceed the observation limit")
            if self._observations.get(item.source.id) is not item:
                return  # Removed/revoked/reconfigured while the request was in flight.
            identity = (result.device_id, result.generation)
            if identity != item.identity:
                item.identity = identity
                item.sequence = 0
                item.segment += 1
                item.sample = None
                item.resources.clear()
            item.system_info = info
            item.interval_ms = result.sample_interval_ms
            item.sample = sample
            item.received_at = time.monotonic()
            item.status = "ready" if sample is not None else "waiting"
            if sample is None:
                item.invalidate_rates()
                return
            if sample.sequence <= item.sequence:
                return
            item.sequence = sample.sequence
            if sample.sample_age_ms > max(30_000, 3 * result.sample_interval_ms):
                item.status = "stale"
                item.invalidate_rates()
                return
            # Use Host receive time minus monotonic sample age, not an untrusted remote wall clock.
            minute = int((time.time() - sample.sample_age_ms / 1000) // 60)
            item.add_rates(sample, minute)
            system = sample.system
            memory = None
            if (
                system.memory_total_bytes is not None
                and system.memory_available_bytes is not None
                and system.memory_available_bytes <= system.memory_total_bytes
            ):
                memory = float(system.memory_total_bytes - system.memory_available_bytes)
            cgroup_memory = None if sample.cgroup_v2 is None else sample.cgroup_v2.memory_current_bytes
            item.history.add(
                minute,
                item.segment,
                (
                    system.cpu_busy_percent,
                    memory,
                    None if cgroup_memory is None else float(cgroup_memory),
                    None if sample.connections is None else sample.connections.tcp_total,
                    None if sample.connections is None else sample.connections.udp_sockets,
                ),
            )
        except Exception:
            item.status = "offline"
            item.invalidate_rates()
