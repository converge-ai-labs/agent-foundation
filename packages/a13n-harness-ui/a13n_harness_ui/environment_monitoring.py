"""Human-facing Device observations with bounded, process-local history."""

from __future__ import annotations

import time
from array import array
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

import anyio
from a13n_envd_client import EIPDeviceConnection
from a13n_envd_client.eip.v1 import MetricsSample, SystemInfo
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import HarnessUiError

MAX_TARGETS = 64
HISTORY_MINUTES = 1440
POLL_SECONDS = 10
COLLECTION_CONCURRENCY = 4
COLLECTION_TIMEOUT = 5
MAX_OBSERVATION_BYTES = 64 * 1024
# minute, connection segment, then (sum, count, peak) for three gauges.
_COLUMNS = 11
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


class EnvironmentObservation(MonitoringModel):
    id: str
    name: str
    transport: Literal["http", "websocket", "stdio"]
    roots: tuple[str, ...] = ()
    status: Status
    system_info: SystemInfo | None = None
    sample: MetricsSample | None = None
    sample_interval_ms: int | None = None
    age_ms: int | None = None


class MonitoringCatalog(MonitoringModel):
    environments: tuple[EnvironmentObservation, ...]
    omitted: int
    capacity: int = MAX_TARGETS
    retention_minutes: int = HISTORY_MINUTES


class MonitoringDetail(MonitoringModel):
    environment: EnvironmentObservation
    history: tuple[HistoryPoint, ...]
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
    """Fixed 126,720-byte ring per Device; no unbounded sample/series objects."""

    def __init__(self) -> None:
        self.values = array("d", [0.0]) * (HISTORY_MINUTES * _COLUMNS)

    def add(self, minute: int, segment: int, gauges: tuple[float | None, ...]) -> None:
        offset = minute % HISTORY_MINUTES * _COLUMNS
        values = self.values
        if values[offset] != minute or values[offset + 1] != segment:
            values[offset : offset + _COLUMNS] = array("d", [float(minute), float(segment)] + [0.0] * 9)
        for index, value in enumerate(gauges):
            if value is None:
                continue
            position = offset + 2 + index * 3
            values[position] += value
            values[position + 1] += 1
            values[position + 2] = max(values[position + 2], value)

    def points(self, now: float) -> tuple[HistoryPoint, ...]:
        minute = int(now // 60)
        points = []
        for timestamp in range(minute - HISTORY_MINUTES + 1, minute + 1):
            offset = timestamp % HISTORY_MINUTES * _COLUMNS
            if self.values[offset] != timestamp or self.values[offset + 1] == 0:
                continue
            gauges: list[Gauge | None] = []
            for index in range(3):
                position = offset + 2 + index * 3
                total, count, peak = self.values[position : position + 3]
                gauges.append(Gauge(average=total / count, peak=peak) if count else None)
            points.append(
                HistoryPoint(
                    minute=timestamp,
                    segment=int(self.values[offset + 1]),
                    cpu_busy_percent=gauges[0],
                    memory_used_bytes=gauges[1],
                    cgroup_memory_bytes=gauges[2],
                )
            )
        return tuple(points)


@dataclass(slots=True)
class _Observation:
    source: MonitoringSource
    status: Status = "waiting"
    system_info: SystemInfo | None = None
    sample: MetricsSample | None = None
    interval_ms: int | None = None
    received_at: float = 0
    identity: tuple[str, int] | None = None
    sequence: int = 0
    segment: int = 0
    history: MinuteHistory = field(default_factory=MinuteHistory)

    def view(self, now: float) -> EnvironmentObservation:
        age = None if self.sample is None else self.sample.sample_age_ms + max(0, int((now - self.received_at) * 1000))
        status = self.status
        if status == "ready" and age is not None and age > max(30_000, 3 * (self.interval_ms or 10_000)):
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
            age_ms=age,
        )


class EnvironmentMonitoring:
    """One App-owned collector; queries never perform remote I/O or start a daemon."""

    def __init__(self, sources: Callable[[], Awaitable[Iterable[MonitoringSource]]]) -> None:
        self._sources = sources
        self._observations: dict[str, _Observation] = {}
        self._omitted = 0
        self._sync_lock = anyio.Lock()

    async def synchronize(self) -> None:
        async with self._sync_lock:
            retained: dict[str, _Observation] = {}
            omitted = 0
            for source in await self._sources():
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
                    previous = _Observation(source=source, status="revoked" if source.revoked else "waiting")
                previous.source = source
                retained[source.id] = previous
            self._observations = retained
            self._omitted = omitted

    async def catalog(self) -> MonitoringCatalog:
        await self.synchronize()
        now = time.monotonic()
        return MonitoringCatalog(
            environments=tuple(item.view(now) for item in self._observations.values()), omitted=self._omitted
        )

    async def detail(self, target_id: str) -> MonitoringDetail:
        await self.synchronize()
        item = self._observations.get(target_id)
        if item is None:
            raise HarnessUiError(
                "The monitored Environment is unavailable or outside the monitoring capacity.",
                code="environment_monitor_missing",
            )
        return MonitoringDetail(environment=item.view(time.monotonic()), history=item.history.points(time.time()))

    async def run(self) -> None:
        while True:
            try:
                await self.collect()
            except Exception:
                # One unavailable configuration read must not terminate the App's other background work.
                _LOGGER.warning("Environment monitoring collection failed")
            await anyio.sleep(POLL_SECONDS)

    async def collect(self) -> None:
        await self.synchronize()
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
            item.system_info = info
            item.interval_ms = result.sample_interval_ms
            item.sample = sample
            item.received_at = time.monotonic()
            item.status = "ready" if sample is not None else "waiting"
            if sample is None or sample.sequence <= item.sequence:
                return
            item.sequence = sample.sequence
            if sample.sample_age_ms > max(30_000, 3 * result.sample_interval_ms):
                return
            # Use Host receive time minus monotonic sample age, not an untrusted remote wall clock.
            minute = int((time.time() - sample.sample_age_ms / 1000) // 60)
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
                (system.cpu_busy_percent, memory, None if cgroup_memory is None else float(cgroup_memory)),
            )
        except Exception:
            item.status = "offline"
