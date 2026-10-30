"""Typed chart catalog shared by temporary history and scoped backend expressions."""

import json
from typing import Literal, get_args

from a13n_envd_client.eip.v1 import MetricsSample

from a13n_service.providers.metrics import Range

Scalar = Literal[
    "system_cpu_busy_ratio",
    "system_memory_used_bytes",
    "cgroup_memory_current_bytes",
    "system_memory_total_bytes",
    "system_memory_available_bytes",
    "system_memory_cache_bytes",
    "system_memory_wired_bytes",
    "system_memory_compressed_bytes",
    "system_swap_used_bytes",
    "system_swap_total_bytes",
    "system_commit_total_bytes",
    "system_commit_limit_bytes",
    "system_processes",
    "system_threads",
    "system_handles",
    "cgroup_pids_current",
    "tcp_connections",
    "udp_sockets",
    "daemon_sessions",
    "daemon_processes",
    "daemon_connections",
    "daemon_transfers",
    "daemon_retained_output_bytes",
]
Rate = Literal[
    "network_received_bytes_per_second",
    "network_transmitted_bytes_per_second",
    "disk_read_bytes_per_second",
    "disk_written_bytes_per_second",
    "disk_read_operations_per_second",
    "disk_write_operations_per_second",
    "cgroup_io_read_bytes_per_second",
    "cgroup_io_written_bytes_per_second",
    "cgroup_io_read_operations_per_second",
    "cgroup_io_write_operations_per_second",
]
Metric = Scalar | Rate
SCALARS: tuple[str, ...] = get_args(Scalar)
RATE_FIELDS = {
    "network": ("received_bytes", "transmitted_bytes"),
    "disk": ("read_bytes", "written_bytes", "read_operations", "write_operations"),
    "cgroup_io": ("read_bytes", "written_bytes", "read_operations", "write_operations"),
}


class MetricQuery(Range):
    metric: Metric
    include_snapshot: bool = False


def expression(metric: Metric, labels: dict[str, str]) -> str:
    selector = "{" + ",".join(f"{name}={json.dumps(value)}" for name, value in sorted(labels.items())) + "}"
    if metric == "system_memory_used_bytes":
        return f"envd_system_memory_total_bytes{selector} - envd_system_memory_available_bytes{selector}"
    if metric.endswith("_per_second"):
        # Keep interface/disk identity and generation labels; never sum stacked sources.
        return f"rate(envd_{metric.removesuffix('_per_second')}_total{selector}[5m])"
    return f"envd_{metric}{selector}"


def scalars(sample: MetricsSample) -> tuple[float | int | None, ...]:
    system, group, connections, daemon = sample.system, sample.cgroup_v2, sample.connections, sample.daemon
    memory = None
    if (
        system.memory_total_bytes is not None
        and system.memory_available_bytes is not None
        and system.memory_available_bytes <= system.memory_total_bytes
    ):
        memory = system.memory_total_bytes - system.memory_available_bytes
    return (
        None if system.cpu_busy_percent is None else system.cpu_busy_percent / 100,
        memory,
        None if group is None else group.memory_current_bytes,
        system.memory_total_bytes,
        system.memory_available_bytes,
        system.memory_cache_bytes,
        system.memory_wired_bytes,
        system.memory_compressed_bytes,
        system.swap_used_bytes,
        system.swap_total_bytes,
        system.commit_total_bytes,
        system.commit_limit_bytes,
        system.process_count,
        system.thread_count,
        system.handle_count,
        None if group is None else group.pids_current,
        None if connections is None else connections.tcp_total,
        None if connections is None else connections.udp_sockets,
        None if daemon is None else daemon.sessions,
        None if daemon is None else daemon.processes,
        None if daemon is None else daemon.connections,
        None if daemon is None else daemon.transfers,
        None if daemon is None else daemon.retained_output_bytes,
    )
