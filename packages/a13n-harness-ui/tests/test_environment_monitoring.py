from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from a13n_envd_client.eip.v1 import DeviceMetricsResult, MetricsSample, SystemInfo, SystemMetrics
from a13n_harness_ui.environment_monitoring import (
    COLLECTION_CONCURRENCY,
    HISTORY_MINUTES,
    MAX_TARGETS,
    EnvironmentMonitoring,
    MinuteHistory,
    MonitoringSource,
)
from a13n_harness_ui.errors import HarnessUiError


def sample(sequence=1, *, cpu=25.0, age=0):
    return MetricsSample(
        sequence=sequence,
        sampled_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        sample_age_ms=age,
        collection_duration_ms=1,
        system=SystemMetrics(cpu_busy_percent=cpu, memory_total_bytes=1000, memory_available_bytes=400),
    )


def setup_monitor(*, generation=1, observed=None):
    result = DeviceMetricsResult(
        device_id="device-test", generation=generation, sample_interval_ms=10000, sample=observed
    )
    device = SimpleNamespace(
        descriptor=SimpleNamespace(available_methods=("device.metrics",), system_info=SystemInfo(os="linux")),
        metrics=AsyncMock(return_value=result),
    )
    device.describe = AsyncMock(side_effect=lambda: device.descriptor)
    acquire = AsyncMock(return_value=device)
    sources = [MonitoringSource(id="build", name="Build", transport="http", revision="one", acquire=acquire)]

    async def current():
        return iter(sources)

    return EnvironmentMonitoring(current), sources, device, acquire


def test_minute_history_is_fixed_size_and_expires_and_segments():
    history = MinuteHistory()
    allocated = history.values.buffer_info()[1] * history.values.itemsize
    assert allocated < 128 * 1024
    for minute in range(10_000, 10_000 + HISTORY_MINUTES * 2):
        history.add(minute, 1, (10.0, None, None))
        history.add(minute, 1, (30.0, 0.0, None))
    latest = 10_000 + HISTORY_MINUTES * 2 - 1
    points = history.points(latest * 60)
    assert len(points) == HISTORY_MINUTES
    assert points[0].minute == latest - HISTORY_MINUTES + 1
    assert points[-1].cpu_busy_percent.average == 20
    assert points[-1].cpu_busy_percent.peak == 30
    assert points[-1].memory_used_bytes.average == 0
    assert points[-1].cgroup_memory_bytes is None
    history.add(latest, 2, (50.0, None, None))
    assert history.points(latest * 60)[-1].cpu_busy_percent.average == 50
    assert history.points(latest * 60)[-1].segment == 2
    assert history.points((latest + HISTORY_MINUTES) * 60) == ()
    assert history.values.buffer_info()[1] * history.values.itemsize == allocated


@pytest.mark.anyio
async def test_queries_do_not_collect_and_repeated_samples_are_not_double_counted(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_800_000_030.0)
    monitor, _, device, acquire = setup_monitor(observed=sample())
    assert (await monitor.catalog()).environments[0].status == "waiting"
    acquire.assert_not_awaited()
    await monitor.collect()
    await monitor.collect()
    first = await monitor.detail("build")
    assert first.environment.status == "ready"
    assert first.environment.system_info.os.value == "linux"
    assert first.history[-1].cpu_busy_percent.average == 25
    device.metrics.return_value = device.metrics.return_value.model_copy(update={"sample": sample(2, cpu=75.0)})
    await monitor.collect()
    detail = await monitor.detail("build")
    assert detail.history[-1].cpu_busy_percent.average == 50
    assert detail.history[-1].cpu_busy_percent.peak == 75
    assert detail.history[-1].memory_used_bytes.average == 600
    assert acquire.await_count == 3
    await monitor.catalog()
    await monitor.detail("build")
    assert acquire.await_count == 3


@pytest.mark.anyio
async def test_no_sample_disabled_stale_offline_and_not_started_are_distinct():
    monitor, _, device, acquire = setup_monitor()
    await monitor.collect()
    assert (await monitor.detail("build")).environment.status == "waiting"
    device.descriptor.available_methods = ()
    await monitor.collect()
    assert (await monitor.detail("build")).environment.status == "disabled"
    device.descriptor.available_methods = ("device.metrics",)
    device.metrics.return_value = device.metrics.return_value.model_copy(update={"sample": sample(age=31_000)})
    await monitor.collect()
    detail = await monitor.detail("build")
    assert detail.environment.status == "stale"
    assert detail.environment.age_ms >= 31_000
    assert detail.history == ()
    acquire.side_effect = TimeoutError
    await monitor.collect()
    assert (await monitor.detail("build")).environment.status == "offline"
    acquire.side_effect = None
    acquire.return_value = None
    await monitor.collect()
    assert (await monitor.detail("build")).environment.status == "not_started"


@pytest.mark.anyio
async def test_generation_change_starts_new_segment_and_configuration_revocation_discards_history():
    monitor, sources, device, _ = setup_monitor(observed=sample())
    await monitor.collect()
    device.metrics.return_value = device.metrics.return_value.model_copy(
        update={"generation": 2, "sample": sample(cpu=70.0)}
    )
    await monitor.collect()
    detail = await monitor.detail("build")
    assert detail.history[-1].segment == 2
    assert detail.history[-1].cpu_busy_percent.average == 70
    sources[0] = replace(sources[0], revoked=True)
    detail = await monitor.detail("build")
    assert detail.environment.status == "revoked"
    assert detail.environment.sample is None
    assert detail.history == ()
    sources.clear()
    with pytest.raises(HarnessUiError, match="unavailable"):
        await monitor.detail("build")


@pytest.mark.anyio
async def test_target_and_worker_limits_and_fencing_in_flight_removal():
    monitor, sources, device, _ = setup_monitor(observed=sample())
    active = 0
    peak = 0
    entered = anyio.Event()
    release = anyio.Event()

    async def acquire():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == COLLECTION_CONCURRENCY:
            entered.set()
        try:
            await release.wait()
            return device
        finally:
            active -= 1

    original = sources[0]
    sources[:] = [replace(original, id=f"device-{index}", acquire=acquire) for index in range(MAX_TARGETS + 10)]
    async with anyio.create_task_group() as group:
        group.start_soon(monitor.collect)
        await entered.wait()
        catalog = await monitor.catalog()
        assert len(catalog.environments) == MAX_TARGETS
        assert catalog.omitted == 10
        sources.clear()
        assert (await monitor.catalog()).environments == ()
        release.set()
    assert peak == COLLECTION_CONCURRENCY
    assert (await monitor.catalog()).environments == ()


@pytest.mark.anyio
async def test_collection_cancellation_releases_in_flight_work():
    monitor, sources, _, _ = setup_monitor()
    entered = anyio.Event()
    stopped = anyio.Event()

    async def acquire():
        entered.set()
        try:
            await anyio.sleep_forever()
        finally:
            stopped.set()

    sources[0] = replace(sources[0], acquire=acquire)
    async with anyio.create_task_group() as group:
        group.start_soon(monitor.run)
        await entered.wait()
        group.cancel_scope.cancel()
    assert stopped.is_set()


@pytest.mark.anyio
async def test_unreadable_values_remain_absent_and_restart_drops_memory_history():
    monitor, sources, _, _ = setup_monitor(observed=sample(cpu=None))
    await monitor.collect()
    assert (await monitor.detail("build")).history[-1].cpu_busy_percent is None

    # A new App/collector starts empty even for the same Device configuration.
    async def current():
        return sources

    restarted = EnvironmentMonitoring(current)
    assert (await restarted.detail("build")).history == ()


def test_ui_sandbox_enables_sampler_while_sdk_and_explicit_launch_remain_opt_in():
    from a13n_harness.providers.environment.local_envd.configuration import LocalEnvdLaunchConfiguration
    from a13n_harness_ui.extensions.environment_adapters import LocalEnvdProfileConfiguration

    assert LocalEnvdLaunchConfiguration().monitoring is None
    assert LocalEnvdProfileConfiguration().launch.monitoring.sample_interval_ms == 10_000
    assert LocalEnvdProfileConfiguration.model_validate({"launch": {}}).launch.monitoring is None


@pytest.mark.anyio
async def test_oversized_observations_are_not_retained_and_deadlines_end_collection(monkeypatch):
    import a13n_harness_ui.environment_monitoring as module

    monitor, sources, _, acquire = setup_monitor(observed=sample())
    monkeypatch.setattr(module, "MAX_OBSERVATION_BYTES", 1)
    await monitor.collect()
    detail = await monitor.detail("build")
    assert detail.environment.status == "offline"
    assert detail.environment.sample is None and detail.history == ()

    async def blocked():
        await anyio.sleep_forever()

    sources[0] = replace(sources[0], acquire=blocked)
    monkeypatch.setattr(module, "COLLECTION_TIMEOUT", 0.01)
    await monitor.collect()
    assert (await monitor.detail("build")).environment.status == "offline"
    assert acquire.await_count == 1
