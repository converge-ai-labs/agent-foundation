"""OTLP projection and bounded delivery without any live collector dependency."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.providers.metrics import Destination
from a13n_service.runs.environments.monitoring.export import Exporter, Projection
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.sdk.metrics.export import Sum

from .test_monitoring_history import NOW, report


def metric(data, name):
    return next(m for m in data.resource_metrics[0].scope_metrics[0].metrics if m.name == name)


def cpu_report(value, *, generation=1):
    result = report(generation=generation)
    return result.model_copy(
        update={
            "sample": result.sample.model_copy(
                update={
                    "system": result.sample.system.model_copy(
                        update={
                            "cpu_time_seconds": result.sample.system.__class__.model_validate(
                                {"cpu_time_seconds": {"user": value}}
                            ).cpu_time_seconds,
                        }
                    ),
                }
            )
        }
    )


def test_projection_uses_standard_names_units_and_authorized_identity():
    projection = Projection("ws_test", "env_test", "device-test")
    data = projection.encode(report(), observed_at=NOW)
    point = metric(data, "envd_system_cpu_busy_ratio").data.data_points[0]
    assert point.value == 0.25
    assert point.time_unix_nano == NOW * 10**9
    assert point.attributes == {
        "workspace_id": "ws_test",
        "environment_id": "env_test",
        "device_id": "device-test",
        "generation": "1",
    }
    assert metric(data, "envd_system_memory_available_bytes").data.data_points[0].value == 400
    names = {m.name for m in data.resource_metrics[0].scope_metrics[0].metrics}
    assert "envd_cgroup_memory_current_bytes" not in names
    assert not projection.encode(report(age=20_001), observed_at=NOW).resource_metrics


def test_counter_first_point_and_reset_preserve_cumulative_semantics():
    projection = Projection("ws_test", "env_test", "device-test")
    first = metric(projection.encode(cpu_report(10.0), observed_at=NOW), "envd_system_cpu_time_seconds_total")
    assert isinstance(first.data, Sum) and first.data.is_monotonic
    start = first.data.data_points[0].start_time_unix_nano
    assert start == first.data.data_points[0].time_unix_nano
    second = metric(projection.encode(cpu_report(20.0), observed_at=NOW + 10), first.name).data.data_points[0]
    assert second.start_time_unix_nano == start and second.value == 20
    reset = metric(projection.encode(cpu_report(5.0), observed_at=NOW + 20), first.name).data.data_points[0]
    assert reset.start_time_unix_nano == (NOW + 20) * 10**9
    restarted = metric(
        projection.encode(cpu_report(25.0, generation=2), observed_at=NOW + 30), first.name
    ).data.data_points[0]
    assert restarted.start_time_unix_nano == (NOW + 30) * 10**9
    assert restarted.attributes["generation"] == "2"


@asynccontextmanager
async def receiver(*, status=200, receipt=None, wait=None):
    calls, tasks = [], []

    async def respond(reader, writer):
        try:
            async with asyncio.timeout(3):
                raw = (await reader.readuntil(b"\r\n\r\n")).decode()
                lines = raw.split("\r\n")
                headers = dict(line.lower().split(": ", 1) for line in lines[1:] if ": " in line)
                body = await reader.readexactly(int(headers["content-length"]))
                calls.append((lines[0], headers, ExportMetricsServiceRequest.FromString(body)))
                if wait:
                    await wait()
                content = (receipt or ExportMetricsServiceResponse()).SerializeToString()
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nContent-Length: {len(content)}\r\n"
                    "Location: http://127.0.0.1:9/leak\r\nConnection: close\r\n\r\n".encode()
                    + content
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader, writer):
        tasks.append(asyncio.create_task(respond(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    async with server:
        try:
            yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1/metrics", calls
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def current():
    return True


@pytest.mark.anyio
async def test_export_queue_is_bounded_and_checks_current_owner():
    exporter = Exporter(EndpointPolicy())
    data = Projection("ws_test", "env_test", "device-test").encode(report(), observed_at=NOW)

    async def expired():
        return False

    async with receiver() as (endpoint, calls):
        destination = Destination(endpoint)
        for _ in range(130):
            exporter.submit(data, destination, expired)
        assert exporter.health().queued == 128
        assert exporter.health().dropped == 2
        task = asyncio.create_task(exporter.run())
        try:
            await asyncio.wait_for(exporter.queue.join(), timeout=3)
            assert exporter.health().dropped == 130
            assert not calls
            exporter.submit(data, destination, current)
            await asyncio.wait_for(exporter.queue.join(), timeout=3)
            assert exporter.health().exported == 1
            assert exporter.health().last_success_at is not None
            assert len(calls) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await exporter.close()


@pytest.mark.anyio
async def test_sources_keep_their_own_endpoint_credentials_and_identity(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=ambient-secret")
    exporter = Exporter(EndpointPolicy())
    async with receiver() as (first, calls_a), receiver() as (second, calls_b):
        headers = {"authorization": "Bearer source-a"}
        exporter.submit(
            Projection("ws_a", "env_a", "device-test").encode(report(), observed_at=NOW),
            Destination(first, headers),
            current,
        )
        headers["authorization"] = "mutated"
        exporter.submit(
            Projection("ws_b", "env_b", "device-test").encode(report(), observed_at=NOW),
            Destination(second),
            current,
        )
        task = asyncio.create_task(exporter.run())
        try:
            await asyncio.wait_for(exporter.queue.join(), timeout=3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await exporter.close()
        assert exporter.health().exported == 2
        assert calls_a[0][1]["authorization"] == "bearer source-a"
        assert "authorization" not in calls_b[0][1]
        for calls, identity in [(calls_a, "env_a"), (calls_b, "env_b")]:
            assert calls[0][1]["content-type"] == "application/x-protobuf"
            point = calls[0][2].resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points[0]
            assert {a.key: a.value.string_value for a in point.attributes}["environment_id"] == identity


@pytest.mark.anyio
@pytest.mark.parametrize("status,partial", [(503, False), (307, False), (200, True)])
async def test_failed_redirected_and_partial_exports_are_observable_without_retry(status, partial):
    receipt = ExportMetricsServiceResponse()
    if partial:
        receipt.partial_success.rejected_data_points = 1
    exporter = Exporter(EndpointPolicy())
    async with receiver(status=status, receipt=receipt) as (endpoint, calls):
        exporter.submit(
            Projection("ws_test", "env_test", "device-test").encode(report(), observed_at=NOW),
            Destination(endpoint),
            current,
        )
        task = asyncio.create_task(exporter.run())
        try:
            await asyncio.wait_for(exporter.queue.join(), timeout=3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await exporter.close()
        assert len(calls) == 1
        assert exporter.health().failed == exporter.health().dropped == 1
        assert exporter.health().exported == 0


@pytest.mark.anyio
async def test_shutdown_discards_queue_and_future_submissions():
    exporter = Exporter(EndpointPolicy())
    data = Projection("ws_test", "env_test", "device-test").encode(report(), observed_at=NOW)
    destination = Destination("http://127.0.0.1:9/v1/metrics")
    exporter.submit(data, destination, current)
    await exporter.close()
    exporter.submit(data, destination, current)
    assert exporter.health().queued == 0
    assert exporter.health().dropped == 2
    await asyncio.wait_for(exporter.queue.join(), timeout=1)


def expanded_report(*, inventory=1, identity="native-1", sequence=1):
    """EIP 0.3 fixture at configurable native inventory cardinality."""
    from a13n_envd_client.eip.v1 import DeviceMetricsResult

    value = report(sequence).model_dump()
    sample = value["sample"]
    sample["system"].update(
        memory_wired_bytes=11,
        memory_compressed_bytes=12,
        memory_cache_bytes=13,
        swap_used_bytes=14,
        swap_total_bytes=15,
        commit_total_bytes=16,
        commit_limit_bytes=17,
        process_count=18,
        thread_count=19,
        handle_count=20,
    )
    sample["network"] = {
        "truncated": False,
        "interfaces": {
            f"eth{i}": {
                "identity": f"{identity}-{i}",
                "received_bytes": 100,
                "transmitted_bytes": 200,
                "received_packets": 30,
                "transmitted_packets": 40,
                "receive_errors": 0,
                "transmit_errors": 0,
                "receive_drops": 1,
                "transmit_drops": 2,
            }
            for i in range(inventory)
        },
    }
    disk = {
        "identity": identity,
        "read_bytes": 1024,
        "written_bytes": 2048,
        "read_operations": 3,
        "write_operations": 4,
        "read_time_ms": 1500,
        "write_time_ms": 2000,
        "busy_time_ms": 2500,
        "weighted_time_ms": 3000,
        "in_progress": 0,
    }
    sample["disks"] = {"truncated": False, "devices": {f"disk{i}": dict(disk) for i in range(inventory)}}
    sample["cgroup_v2"] = {
        "pids_current": 3,
        "pids_limit": 100,
        "pids_unlimited": False,
        "memory_events": {},
        "pressure": {},
        "io": sample["disks"],
    }
    sample["connections"] = {"tcp_total": 3, "udp_sockets": 4, "tcp_states": {"established": 2, "listen": 1}}
    sample["daemon"] = {"sessions": 5, "processes": None, "transfers": 0, "connections": 6, "retained_output_bytes": 7}
    sample["local_filesystems"] = {
        "truncated": False,
        "filesystems": {
            f"volume{i}": {
                "metrics": {
                    "total_bytes": 1000,
                    "free_bytes": 400,
                    "available_bytes": 300,
                    "total_inodes": 100,
                    "free_inodes": 40,
                    "available_inodes": 30,
                }
            }
            for i in range(min(inventory, 16))
        },
    }
    return DeviceMetricsResult.model_validate(value)


def test_expanded_projection_preserves_native_units_identity_and_absence():
    projection = Projection("ws_test", "env_test", "device-test")
    data = projection.encode(expanded_report(), observed_at=NOW)
    names = {m.name for m in data.resource_metrics[0].scope_metrics[0].metrics}
    expected = {
        "envd_system_memory_wired_bytes": 11,
        "envd_system_swap_used_bytes": 14,
        "envd_system_commit_total_bytes": 16,
        "envd_system_processes": 18,
        "envd_network_received_bytes_total": 100,
        "envd_network_receive_errors_total": 0,
        "envd_disk_read_time_seconds_total": 1.5,
        "envd_disk_in_progress": 0,
        "envd_cgroup_io_weighted_time_seconds_total": 3,
        "envd_cgroup_pids_limit_unlimited": 0,
        "envd_tcp_connections": 3,
        "envd_udp_sockets": 4,
        "envd_daemon_sessions": 5,
        "envd_daemon_transfers": 0,
        "envd_daemon_retained_output_bytes": 7,
        "envd_filesystem_available_bytes": 300,
    }
    for name, value in expected.items():
        assert metric(data, name).data.data_points[0].value == value, name
    assert "envd_daemon_processes" not in names  # Unavailable worker totals must not become zero.
    assert isinstance(metric(data, "envd_network_received_bytes_total").data, Sum)
    assert not isinstance(metric(data, "envd_disk_in_progress").data, Sum)
    assert metric(data, "envd_network_received_bytes_total").data.data_points[0].attributes["interface"] == "eth0"
    assert metric(data, "envd_network_received_bytes_total").data.data_points[0].attributes["identity"] == "native-1-0"
    assert (
        metric(data, "envd_filesystem_available_bytes").data.data_points[0].attributes["filesystem"] == "auto:volume0"
    )


def test_counter_disappearance_and_identity_churn_reset_and_bound_projection():
    projection = Projection("ws_test", "env_test", "device-test")
    first = projection.encode(expanded_report(), observed_at=NOW)
    baseline_size = len(projection.counters)
    name = "envd_network_received_bytes_total"
    start = metric(first, name).data.data_points[0].start_time_unix_nano
    same = projection.encode(expanded_report(sequence=2), observed_at=NOW + 10)
    assert metric(same, name).data.data_points[0].start_time_unix_nano == start
    for index in range(50):
        data = projection.encode(expanded_report(identity=f"replacement-{index}"), observed_at=NOW + 20 + index)
        assert len(projection.counters) == baseline_size
        assert metric(data, name).data.data_points[0].start_time_unix_nano == (NOW + 20 + index) * 10**9
    projection.encode(report(), observed_at=NOW + 100)
    assert not projection.counters
    returned = projection.encode(expanded_report(identity="replacement-49"), observed_at=NOW + 110)
    assert metric(returned, name).data.data_points[0].start_time_unix_nano == (NOW + 110) * 10**9
    projection.encode(report(age=20001), observed_at=NOW + 120)
    assert not projection.counters


@pytest.mark.anyio
async def test_full_inventory_export_obeys_byte_budget_including_inflight(monkeypatch):
    from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics

    data = Projection("ws_test", "env_test", "device-test").encode(expanded_report(inventory=64), observed_at=NOW)
    size = len(encode_metrics(data).SerializeToString())
    assert 256 * 1024 < size < Exporter.MAX_BYTES
    assert Exporter.MAX_BUFFERED_BYTES == 32 * 1024 * 1024
    exporter = Exporter(EndpointPolicy())
    monkeypatch.setattr(exporter, "MAX_BUFFERED_BYTES", size * 2)
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait():
        entered.set()
        await release.wait()

    async with receiver(wait=wait) as (endpoint, calls):
        exporter.submit(data, Destination(endpoint), current)
        task = asyncio.create_task(exporter.run())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert exporter.queue.empty() and exporter.buffered_bytes == size
            exporter.submit(data, Destination(endpoint), current)
            exporter.submit(data, Destination(endpoint), current)
            assert exporter.queue.qsize() == 1 and exporter.dropped == 1
            assert exporter.buffered_bytes == size * 2
            release.set()
            await asyncio.wait_for(exporter.queue.join(), 3)
            assert exporter.buffered_bytes == 0
            assert exporter.exported == 2
            assert len(calls[0][2].resource_metrics[0].scope_metrics[0].metrics) > 50
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        exporter.submit(data, Destination(endpoint), current)
        assert exporter.buffered_bytes == size
        await exporter.close()
        assert exporter.buffered_bytes == 0


@pytest.mark.anyio
async def test_slow_destination_does_not_block_others_and_shutdown_accounts_in_flight():
    from a13n_service.runs.environments.monitoring.export import ExportHealth

    exporter = Exporter(EndpointPolicy())
    data = Projection("ws_test", "env_test", "device-test").encode(report(), observed_at=NOW)
    blocked, fast = ExportHealth(), ExportHealth()
    started, delivered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    arrivals = 0

    async def slow():
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            started.set()
        await release.wait()

    async def quick():
        delivered.set()

    async with receiver(wait=slow) as (slow_url, _), receiver(wait=quick) as (fast_url, _):
        for _ in range(16):
            exporter.submit(data, Destination(slow_url), current, blocked)
        exporter.submit(data, Destination(fast_url), current, fast)
        task = asyncio.create_task(exporter.run())
        try:
            await asyncio.wait_for(asyncio.gather(started.wait(), delivered.wait()), 2)
            assert arrivals == 2
            assert not release.is_set()
            # Wait for the fast response to complete, not just reach its receiver.
            async with asyncio.timeout(2):
                while fast.exported != 1:
                    await asyncio.sleep(0)
            assert blocked.queued == 16 and fast.queued == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await exporter.close()
        assert exporter.buffered_bytes == 0
        assert exporter.health().queued == 0
        assert blocked.queued == 0 and blocked.dropped == 16
        assert fast.exported == 1 and fast.dropped == 0
        await asyncio.wait_for(exporter.queue.join(), 1)
