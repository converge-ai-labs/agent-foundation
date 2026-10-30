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

    def current():
        return iter(sources)

    return EnvironmentMonitoring(current), sources, device, acquire


def test_minute_history_is_fixed_size_and_expires_and_segments():
    history = MinuteHistory()
    allocated = history.values.buffer_info()[1] * history.values.itemsize
    assert allocated < 200 * 1024
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
    def current():
        return sources

    restarted = EnvironmentMonitoring(current)
    assert (await restarted.detail("build")).history == ()


def test_ui_sandbox_enables_sampler_while_sdk_and_explicit_launch_remain_opt_in():
    from a13n_harness.providers.environment.local_envd.configuration import LocalEnvdLaunchConfiguration
    from a13n_harness_ui.extensions.environment_adapters import LocalEnvdProfileConfiguration

    assert LocalEnvdLaunchConfiguration().monitoring is None
    assert LocalEnvdProfileConfiguration().launch.monitoring.sample_interval_ms == 60_000
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


def test_resource_rates_preserve_zero_reset_and_missing_observations():
    from a13n_harness_ui.environment_monitoring import _ResourceRates

    resource = _ResourceRates(kind="network", name="eth0", identity="if-1", ring=MinuteHistory(2))
    resource.add((100, 200), 10, 10000, 30)
    assert resource.view(10000 * 60).history[-1].rates == {}
    resource.add((200, 200), 20, 10001, 30)
    point = resource.view(10001 * 60).history[-1]
    assert point.rates["received_bytes_per_second"].average == 10
    assert point.rates["transmitted_bytes_per_second"].average == 0
    resource.add((None, 300), 30, 10002, 30)
    resource.add((400, 400), 40, 10003, 30)
    assert "received_bytes_per_second" not in resource.view(10003 * 60).history[-1].rates
    resource.add((1, 1), 50, 10004, 30)
    reset = resource.view(10004 * 60).history[-1]
    assert reset.segment != point.segment
    assert reset.rates == {}
    resource.add((100, 100), 100, 10005, 30)
    assert resource.view(10005 * 60).history[-1].rates == {}
    resource.invalidate()
    resource.add((200, 200), 110, 10006, 30)
    assert resource.view(10006 * 60).history[-1].rates == {}


def test_rate_source_churn_is_bounded_and_identity_change_discards_old_history():
    from a13n_envd_client.eip.v1 import DiskIoMetrics, DiskMetrics, NetworkInterfaceMetrics, NetworkMetrics
    from a13n_harness_ui.environment_monitoring import MAX_RATE_SOURCES, _Observation

    source = MonitoringSource(id="x", name="x", transport="http", revision="x", acquire=AsyncMock())
    item = _Observation(source=source, interval_ms=10000, received_at=100)
    first = sample().model_copy(
        update={
            "network": NetworkMetrics(
                interfaces={
                    f"eth{i}": NetworkInterfaceMetrics(identity=f"if-{i}", received_bytes=100) for i in range(64)
                },
                truncated=False,
            ),
            "disks": DiskMetrics(
                devices={f"disk{i}": DiskIoMetrics(identity=f"disk-{i}", read_bytes=100) for i in range(64)},
                truncated=False,
            ),
        }
    )
    item.add_rates(first, 10000)
    assert len(item.resources) == 2 * MAX_RATE_SOURCES
    assert item.omitted_rate_sources == 128 - 2 * MAX_RATE_SOURCES
    assert sum(len(r.ring.values) * r.ring.values.itemsize for r in item.resources.values()) < 1024 * 1024
    changed = first.model_copy(
        update={
            "network": NetworkMetrics(
                interfaces={"eth0": NetworkInterfaceMetrics(identity="replacement", received_bytes=200)},
                truncated=False,
            )
        }
    )
    item.received_at += 10
    item.add_rates(changed, 10001)
    replacement = item.resources[("network", "eth0")].view(10001 * 60)
    assert replacement.identity == "replacement"
    assert len(replacement.history) == 1
    assert replacement.history[0].rates == {}
    for i in range(20):
        item.received_at += 10
        item.add_rates(
            first.model_copy(
                update={
                    "network": NetworkMetrics(
                        interfaces={f"new{i}": NetworkInterfaceMetrics(identity=str(i), received_bytes=0)},
                        truncated=False,
                    )
                }
            ),
            10002 + i,
        )
        assert len(item.resources) <= 2 * MAX_RATE_SOURCES


@pytest.mark.anyio
async def test_rate_collection_deduplicates_and_breaks_across_offline_and_generation(monkeypatch):
    from a13n_envd_client.eip.v1 import NetworkInterfaceMetrics, NetworkMetrics

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000 + clock[0])

    def observed(sequence, received):
        return sample(sequence).model_copy(
            update={
                "network": NetworkMetrics(
                    interfaces={"eth0": NetworkInterfaceMetrics(identity="if-1", received_bytes=received)},
                    truncated=False,
                )
            }
        )

    monitor, _, device, acquire = setup_monitor(observed=observed(1, 100))
    await monitor.collect()
    clock[0] += 10
    device.metrics.return_value = device.metrics.return_value.model_copy(update={"sample": observed(2, 200)})
    await monitor.collect()
    before = (await monitor.detail("build")).resources
    assert before[0].history[-1].rates["received_bytes_per_second"].average == 10
    await monitor.collect()
    assert (await monitor.detail("build")).resources == before
    acquire.side_effect = TimeoutError
    await monitor.collect()
    acquire.side_effect = None
    clock[0] += 10
    device.metrics.return_value = device.metrics.return_value.model_copy(update={"sample": observed(3, 300)})
    await monitor.collect()
    assert (await monitor.detail("build")).resources[0].history[-1].rates == {}
    device.metrics.return_value = device.metrics.return_value.model_copy(
        update={"generation": 2, "sample": observed(1, 400)}
    )
    clock[0] += 10
    await monitor.collect()
    assert (await monitor.detail("build")).resources[0].history[-1].rates == {}


@pytest.mark.anyio
async def test_minute_polling_preserves_fast_remote_rates_and_cache_freshness(monkeypatch):
    from a13n_envd_client.eip.v1 import NetworkInterfaceMetrics, NetworkMetrics

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000 + clock[0])

    def observed(sequence, received, age=0):
        return sample(sequence, age=age).model_copy(
            update={
                "network": NetworkMetrics(
                    interfaces={"wlan0": NetworkInterfaceMetrics(identity="wifi", received_bytes=received)},
                    truncated=False,
                )
            }
        )

    monitor, _, device, _ = setup_monitor(observed=observed(1, 100))
    await monitor.collect()
    clock[0] += 45
    assert (await monitor.detail("build")).environment.status == "ready"
    clock[0] += 15
    device.metrics.return_value = device.metrics.return_value.model_copy(update={"sample": observed(7, 700)})
    await monitor.collect()
    detail = await monitor.detail("build")
    assert detail.retention_minutes == 60
    assert detail.environment.collection_interval_seconds == 60
    assert detail.resources[0].history[-1].rates["received_bytes_per_second"].average == 10
    # An already stale remote sample is still rejected at receipt.
    device.metrics.return_value = device.metrics.return_value.model_copy(update={"sample": observed(8, 800, 31_000)})
    await monitor.collect()
    assert (await monitor.detail("build")).environment.status == "stale"


@pytest.mark.anyio
async def test_stalled_device_does_not_block_cached_queries_or_healthy_device(monkeypatch):
    import a13n_harness_ui.environment_monitoring as module

    monitor, sources, device, _ = setup_monitor(observed=sample())
    await monitor.collect()
    entered = anyio.Event()
    exited = anyio.Event()

    async def blocked():
        entered.set()
        try:
            await anyio.sleep_forever()
        finally:
            exited.set()

    sources.append(replace(sources[0], id="blocked", acquire=blocked))
    monkeypatch.setattr(module, "COLLECTION_TIMEOUT", 0.05)
    async with anyio.create_task_group() as group:
        group.start_soon(monitor.collect)
        await entered.wait()
        with anyio.fail_after(0.5):
            assert len((await monitor.catalog()).environments) == 2
            assert (await monitor.detail("build")).history
            await exited.wait()
    assert (await monitor.detail("build")).environment.status == "ready"
    assert (await monitor.detail("blocked")).environment.status == "offline"
    assert device.metrics.await_count == 2


@pytest.mark.anyio
async def test_collection_loop_uses_configured_pause_and_does_not_overlap(monkeypatch):
    import a13n_harness_ui.environment_monitoring as module

    monitor, _, _, _ = setup_monitor()
    monitor._poll_seconds = 30
    monitor.collect = AsyncMock()

    async def pause(seconds):
        assert seconds == 30
        monitor.collect.assert_awaited_once()
        raise StopAsyncIteration

    monkeypatch.setattr(module.anyio, "sleep", pause)
    with pytest.raises(StopAsyncIteration):
        await monitor.run()


@pytest.mark.anyio
@pytest.mark.parametrize("profile_id", ["environment-sandbox", "environment-envd", "environment-desktop", "custom"])
async def test_configured_monitoring_cadence_only_overrides_builtin_launches(tmp_path, monkeypatch, profile_id):
    import a13n_harness_ui.envd_runtime as module
    from a13n_harness_ui.composition import ResolvedEnvironmentProfile
    from a13n_harness_ui.configuration.models import EnvdConfiguration
    from a13n_harness_ui.environment_profiles import built_in_environment_profile
    from a13n_harness_ui.settings import EnvdRuntimeSettings

    manager = module.EnvdRuntimeManager(
        settings=EnvdRuntimeSettings(),
        runtime_parent=tmp_path / "runtime",
        configuration=EnvdConfiguration(computer_use=True, monitoring_interval_seconds=30),
    )
    monkeypatch.setattr(manager, "resolve_executable", AsyncMock(return_value=tmp_path / "a13n-envd"))
    launches = []

    def create(executable, **kwargs):
        launches.append(kwargs["launch"])
        return SimpleNamespace()

    monkeypatch.setattr(module, "create_sandbox_runtime", create)
    builtin = built_in_environment_profile(profile_id)
    provider = (
        {"launch": {"monitoring": {"sample_interval_ms": 1000}}} if builtin is None else builtin.provider_configuration
    )
    profile = ResolvedEnvironmentProfile(
        profile_id=profile_id,
        behavior_digest="0" * 64,
        provider_key="local-envd",
        adapter_key="local-envd-project",
        provider_configuration=provider,
    )
    await manager.runtime((tmp_path,), profile=profile)
    assert launches[0].monitoring.sample_interval_ms == (1000 if profile_id == "custom" else 30_000)


@pytest.mark.parametrize("seconds", [0, 9, 61])
def test_monitoring_configuration_bounds(seconds):
    from a13n_harness_ui.configuration.models import EnvdConfiguration
    from pydantic import ValidationError

    assert EnvdConfiguration().monitoring_interval_seconds == 60
    with pytest.raises(ValidationError):
        EnvdConfiguration(monitoring_interval_seconds=seconds)
