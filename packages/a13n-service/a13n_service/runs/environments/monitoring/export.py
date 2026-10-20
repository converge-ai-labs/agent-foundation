"""Project cached EIP observations into OTLP, independently of Console chart retention."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Literal

import anyio
from a13n_envd_client.eip.v1 import DeviceMetricsResult, DiskMetrics, LimitedCpuLimit, LimitedMemoryLimit, Pressures
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_logging import exception_details, get_logger
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceResponse
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Gauge,
    Metric,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from pydantic import BaseModel

from a13n_service.infra.outbound import open_http
from a13n_service.providers.metrics import Destination

logger = get_logger(__name__)
type Outcome = Literal["exported", "failed", "dropped"]


@dataclass(slots=True)
class Projection:
    """One Device owner's cumulative stream, reset on generation changes and counter regressions."""

    workspace_id: str
    target_id: str
    device_id: str
    identity_label: str = "environment_id"
    generation: int | None = None
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[int | float, int]] = field(default_factory=dict)

    def encode(self, report: DeviceMetricsResult, *, observed_at: float) -> MetricsData:
        if report.device_id != self.device_id:
            raise ValueError("Device identity changed")
        if report.generation != self.generation:
            self.counters.clear()
            self.generation = report.generation
        sample = report.sample
        if sample is None or sample.sample_age_ms > 2 * report.sample_interval_ms:
            self.counters.clear()
            return MetricsData(())
        timestamp = int(observed_at * 1_000_000_000)
        identity = {
            "workspace_id": self.workspace_id,
            self.identity_label: self.target_id,
            "device_id": self.device_id,
            "generation": str(report.generation),
        }
        families: dict[str, tuple[bool, list[NumberDataPoint]]] = {}
        current_counters: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[int | float, int]] = {}

        def record(name: str, value: int | float | None, *, counter: bool = False, **labels: str) -> None:
            if value is None:
                return
            start = 0
            if counter:
                key = (name, tuple(sorted(labels.items())))
                previous = self.counters.get(key)
                # The native counter's actual start is unknown. The first point is a
                # zero-duration cumulative point, never an invented zero/reset value.
                start = timestamp if previous is None or value < previous[0] else previous[1]
                current_counters[key] = (value, start)
            _, points = families.setdefault(name, (counter, []))
            points.append(
                NumberDataPoint(identity | labels, start, timestamp, float(value) if value > 2**63 - 1 else value)
            )

        record("sample_sequence", sample.sequence)
        record("sample_interval_seconds", report.sample_interval_ms / 1000)
        record("sample_age_seconds", sample.sample_age_ms / 1000)
        record("collection_duration_seconds", sample.collection_duration_ms / 1000)
        system = sample.system
        record("system_cpu_busy_ratio", None if system.cpu_busy_percent is None else system.cpu_busy_percent / 100)
        record("system_logical_cpus", system.logical_cpu_count)
        record("system_memory_total_bytes", system.memory_total_bytes)
        record("system_memory_available_bytes", system.memory_available_bytes)
        if system.cpu_time_seconds is not None:
            for mode, value in system.cpu_time_seconds.model_dump(exclude_none=True).items():
                record("system_cpu_time_seconds_total", value, counter=True, mode=mode)

        def pressures(scope: str, values: Pressures | None) -> None:
            if values is None:
                return
            for resource, pressure in (("cpu", values.cpu), ("memory", values.memory), ("io", values.io)):
                if pressure is None:
                    continue
                for level, value in (("some", pressure.some), ("full", pressure.full)):
                    if value is None:
                        continue
                    for window, percent in (
                        ("10", value.avg10_percent),
                        ("60", value.avg60_percent),
                        ("300", value.avg300_percent),
                    ):
                        record(
                            f"{scope}_pressure_ratio",
                            percent / 100,
                            resource=resource,
                            level=level,
                            window=window,
                        )
                    record(
                        f"{scope}_pressure_stall_seconds_total",
                        value.total_usec / 1_000_000,
                        counter=True,
                        resource=resource,
                        level=level,
                    )

        pressures("system", system.pressure)
        group = sample.cgroup_v2
        if group is not None:
            for name, value in (
                ("usage", group.cpu_usage_usec),
                ("user", group.cpu_user_usec),
                ("system", group.cpu_system_usec),
                ("throttled", group.cpu_throttled_usec),
            ):
                record(f"cgroup_cpu_{name}_seconds_total", None if value is None else value / 1_000_000, counter=True)
            record("cgroup_cpu_periods_total", group.cpu_periods_total, counter=True)
            record("cgroup_cpu_throttled_periods_total", group.cpu_throttled_periods_total, counter=True)
            if group.cpu_max is not None:
                record("cgroup_cpu_limit_unlimited", int(not isinstance(group.cpu_max, LimitedCpuLimit)))
                if isinstance(group.cpu_max, LimitedCpuLimit):
                    record("cgroup_cpu_quota_seconds", group.cpu_max.quota_usec / 1_000_000)
                    record("cgroup_cpu_period_seconds", group.cpu_max.period_usec / 1_000_000)
            record("cgroup_memory_current_bytes", group.memory_current_bytes)
            if group.memory_max is not None:
                record("cgroup_memory_limit_unlimited", int(not isinstance(group.memory_max, LimitedMemoryLimit)))
                if isinstance(group.memory_max, LimitedMemoryLimit):
                    record("cgroup_memory_limit_bytes", group.memory_max.bytes)
            for event, value in group.memory_events.model_dump(exclude_none=True).items():
                record("cgroup_memory_events_total", value, counter=True, event=event)
            pressures("cgroup", group.pressure)
        for name, value in (
            ("memory_wired_bytes", system.memory_wired_bytes),
            ("memory_compressed_bytes", system.memory_compressed_bytes),
            ("memory_cache_bytes", system.memory_cache_bytes),
            ("swap_used_bytes", system.swap_used_bytes),
            ("swap_total_bytes", system.swap_total_bytes),
            ("commit_total_bytes", system.commit_total_bytes),
            ("commit_limit_bytes", system.commit_limit_bytes),
            ("processes", system.process_count),
            ("threads", system.thread_count),
            ("handles", system.handle_count),
        ):
            record(f"system_{name}", value)
        if sample.network is not None:
            record("network_truncated", int(sample.network.truncated))
            for interface, network in sample.network.interfaces.items():
                for name, value in network.model_dump(exclude={"identity"}, exclude_none=True).items():
                    record(f"network_{name}_total", value, counter=True, interface=interface, identity=network.identity)

        def disks(prefix: str, values: DiskMetrics | None) -> None:
            if values is None:
                return
            record(f"{prefix}_truncated", int(values.truncated))
            for device, disk in values.devices.items():
                for name, value in disk.model_dump(exclude={"identity"}, exclude_none=True).items():
                    counter = name != "in_progress"
                    if name.endswith("_ms"):
                        name, value = name.removesuffix("_ms") + "_seconds", value / 1000
                    record(
                        f"{prefix}_{name}" + ("_total" if counter else ""),
                        value,
                        counter=counter,
                        disk=device,
                        identity=disk.identity,
                    )

        disks("disk", sample.disks)
        if group is not None:
            record("cgroup_pids_current", group.pids_current)
            record("cgroup_pids_limit", group.pids_limit)
            record("cgroup_pids_limit_unlimited", None if group.pids_unlimited is None else int(group.pids_unlimited))
            disks("cgroup_io", group.io)
        if sample.connections is not None:
            record("tcp_connections", sample.connections.tcp_total)
            record("udp_sockets", sample.connections.udp_sockets)
            for state, count in sample.connections.tcp_states.items():
                record("tcp_state_connections", count, state=state)
        if sample.daemon is not None:
            for name, value in sample.daemon.model_dump(exclude_none=True).items():
                record(f"daemon_{name}", value)
        filesystems = dict(sample.filesystems)
        if sample.local_filesystems is not None:
            record("local_filesystems_truncated", int(sample.local_filesystems.truncated))
            filesystems.update({f"auto:{name}": value for name, value in sample.local_filesystems.filesystems.items()})
        for target, observation in filesystems.items():
            record("filesystem_available", int(observation.metrics is not None), filesystem=target)
            if observation.metrics is not None:
                for name, value in observation.metrics.model_dump(exclude_none=True).items():
                    record(f"filesystem_{name}", value, filesystem=target)
        # Disappeared counters lose continuity. Keeping only this observation's
        # keys also bounds state under interface/device identity churn.
        self.counters = current_counters
        metrics = [
            Metric(
                f"envd_{name}",
                "Envd cached Device observation",
                "",
                Sum(points, AggregationTemporality.CUMULATIVE, True) if counter else Gauge(points),
            )
            for name, (counter, points) in families.items()
        ]
        return MetricsData(
            (
                ResourceMetrics(
                    Resource(
                        {
                            "service.name": "a13n-envd",
                            "service.instance.id": self.target_id,
                            "a13n.workspace.id": self.workspace_id,
                            "a13n." + self.identity_label.replace("_", "."): self.target_id,
                        }
                    ),
                    (ScopeMetrics(InstrumentationScope("a13n-service.environment-monitoring"), metrics, ""),),
                    "",
                ),
            )
        )


class ExportHealth(BaseModel):
    queued: int = 0
    exported: int = 0
    failed: int = 0
    dropped: int = 0
    last_success_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Pending:
    body: bytes = field(repr=False)
    destination: Destination = field(repr=False)
    current: Callable[[], Awaitable[bool]] = field(repr=False)
    health: ExportHealth | None = field(default=None, repr=False)


class Exporter:
    """A process-wide viewing-buffer outlet: bounded, best effort and never a durable outbox.

    Each item carries its source destination and a fence checked immediately before I/O.
    The upstream OTel encoder owns the protobuf; Service owns async HTTP, TLS and endpoint policy.
    Failed or ambiguous deliveries are dropped rather than replayed into a changed source.
    """

    # EIP 0.3's bounded network/disk inventories can exceed 256 KiB after
    # attaching series labels. Retain the original 32 MiB process budget while
    # allowing a larger single observation, including the in-flight request.
    MAX_BYTES = 1048576
    MAX_BUFFERED_BYTES = 32 * 1024 * 1024

    def __init__(self, policy: EndpointPolicy, *, timeout: float = 5) -> None:
        self.policy, self.timeout = policy, timeout
        self.queue: asyncio.Queue[Pending] = asyncio.Queue(maxsize=128)
        self.exported = self.failed = self.dropped = 0
        self.buffered_bytes = 0
        self.last_success: float | None = None
        self.closed = False
        self.wake = asyncio.Event()

    def submit(
        self,
        data: MetricsData,
        destination: Destination,
        current: Callable[[], Awaitable[bool]],
        health: ExportHealth | None = None,
    ) -> None:
        if not data.resource_metrics:
            return
        if self.closed or self.queue.full():
            self.dropped += 1
            if health is not None:
                health.dropped += 1
            return
        body = encode_metrics(data).SerializeToString()
        if len(body) > self.MAX_BYTES or self.buffered_bytes + len(body) > self.MAX_BUFFERED_BYTES:
            self.dropped += 1
            if health is not None:
                health.dropped += 1
            return
        # Freeze headers as well as the encoded observation; the caller may reuse its dictionaries.
        destination = Destination(destination.endpoint, dict(destination.headers))
        if health is not None:
            health.queued += 1
        self.queue.put_nowait(Pending(body, destination, current, health))
        self.buffered_bytes += len(body)
        self.wake.set()

    def health(self) -> ExportHealth:
        return ExportHealth(
            queued=self.queue.qsize(),
            exported=self.exported,
            failed=self.failed,
            dropped=self.dropped,
            last_success_at=None if self.last_success is None else datetime.fromtimestamp(self.last_success, UTC),
        )

    async def run(self) -> None:
        active: dict[str, int] = {}

        def finished(item: Pending, task: asyncio.Task[Outcome]) -> None:
            # Completion callbacks also account for cancellation before a task first runs.
            outcome = "dropped" if task.cancelled() else task.result()
            succeeded, failed = outcome == "exported", outcome == "failed"
            self.exported += int(succeeded)
            self.failed += int(failed)
            self.dropped += int(not succeeded)
            if succeeded:
                self.last_success = time.time()
            if item.health is not None:
                item.health.queued -= 1
                item.health.exported += int(succeeded)
                item.health.failed += int(failed)
                item.health.dropped += int(not succeeded)
                if succeeded:
                    item.health.last_success_at = datetime.now(UTC)
            self.buffered_bytes -= len(item.body)
            self.queue.task_done()
            endpoint = item.destination.endpoint
            active[endpoint] -= 1
            if not active[endpoint]:
                del active[endpoint]
            self.wake.set()

        async with asyncio.TaskGroup() as tasks:
            while True:
                await self.wake.wait()
                self.wake.clear()
                # Scan at most one bounded queue lap. Busy destinations cannot occupy
                # every worker or hide another destination behind their queued items.
                for _ in range(self.queue.qsize()):
                    if sum(active.values()) >= 8:
                        break
                    item = self.queue.get_nowait()
                    endpoint = item.destination.endpoint
                    if active.get(endpoint, 0) >= 2:
                        self.queue.put_nowait(item)
                        self.queue.task_done()
                        continue
                    active[endpoint] = active.get(endpoint, 0) + 1
                    task = tasks.create_task(self.deliver(item))
                    task.add_done_callback(partial(finished, item))

    async def deliver(self, item: Pending) -> Outcome:
        try:
            with anyio.fail_after(self.timeout):
                if not await item.current():
                    return "dropped"
                async with open_http(self.policy, timeout=self.timeout, max_bytes=65536) as client:
                    response = await client.post(
                        item.destination.endpoint,
                        content=item.body,
                        headers={**item.destination.headers, "content-type": "application/x-protobuf"},
                    )
                    response.raise_for_status()
                    receipt = ExportMetricsServiceResponse.FromString(response.content)
                    failed = bool(receipt.partial_success.rejected_data_points or receipt.partial_success.error_message)
                    return "failed" if failed else "exported"
        except Exception as error:
            logger.warning("Environment metrics export failed", extra={"exception_details": exception_details(error)})
            return "failed"

    async def close(self) -> None:
        # The lifespan owner cancels run() first. Discard queued samples and their credentials.
        self.closed = True
        while not self.queue.empty():
            item = self.queue.get_nowait()
            if item.health is not None:
                item.health.queued -= 1
                item.health.dropped += 1
            self.buffered_bytes -= len(item.body)
            self.queue.task_done()
            self.dropped += 1
