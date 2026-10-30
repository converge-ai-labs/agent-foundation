"""Temporary chart retention, sample identity and missing/stale observations."""

from datetime import UTC, datetime

import pytest
from a13n_envd_client.eip.v1 import (
    DeviceMetricsResult,
    MetricsSample,
    NetworkInterfaceMetrics,
    NetworkMetrics,
    SystemMetrics,
)
from a13n_service.providers.metrics_catalog import SCALARS, MetricQuery, expression
from a13n_service.runs.environments.monitoring.history import RETENTION_MINUTES, Cache, MinuteHistory

NOW = 1_800_000_000


def report(sequence=1, *, generation=1, age=0, cpu=25.0, available=400):
    return DeviceMetricsResult(
        device_id="device-test",
        generation=generation,
        sample_interval_ms=10_000,
        sample=MetricsSample(
            sequence=sequence,
            sampled_at=datetime.fromtimestamp(NOW - 3600, UTC).isoformat().replace("+00:00", "Z"),
            sample_age_ms=age,
            collection_duration_ms=1,
            system=SystemMetrics(cpu_busy_percent=cpu, memory_total_bytes=1000, memory_available_bytes=available),
        ),
    )


def points(cache, *, now=NOW, metric="system_cpu_busy_ratio", step=60):
    result = cache.read(MetricQuery(metric=metric, start=now - 86400, end=now, step=step), now=now)
    return result.series[0].points if result.series else []


def test_history_is_bounded_expires_and_keeps_average_and_peak():
    history = MinuteHistory()
    for offset in range(RETENTION_MINUTES * 2):
        now = NOW + offset * 60
        history.add(now, 1, (0.1, 0.0, None))
        history.add(now, 1, (0.5, None, None))
    result = history.read(now=now, start=NOW, end=now)
    assert history.values.buffer_info()[1] * history.values.itemsize < 128 * 1024
    cpu, memory = result.series
    assert len(cpu.points) == RETENTION_MINUTES
    assert cpu.points[0][0] == now - (RETENTION_MINUTES - 1) * 60
    assert cpu.points[-1][2:] == (0.3, 0.5)
    assert memory.metric == "system_memory_used_bytes" and memory.points[-1][2] == 0.0
    assert all(series.metric != "cgroup_memory_current_bytes" for series in result.series)
    assert result.temporary and result.source == "temporary"
    assert not history.read(now=now + 86400, start=NOW, end=now + 86400).series


def test_repeated_samples_are_not_counted_or_exported_twice():
    cache = Cache("device-test")
    assert cache.accept(report(), now=NOW, monotonic=10)
    assert not cache.accept(report(cpu=90.0), now=NOW + 1, monotonic=11)
    assert cache.accept(report(2, cpu=75.0), now=NOW + 2, monotonic=12)
    assert points(cache)[0][2] == 0.5
    assert points(cache, metric="system_memory_used_bytes")[0][2] == 600


def test_generation_starts_a_new_segment_without_blending_same_minute():
    cache = Cache("device-test")
    assert cache.accept(report(20), now=NOW, monotonic=10)
    assert cache.accept(report(1, generation=2, cpu=75.0), now=NOW + 1, monotonic=11)
    assert points(cache)[0][2] == 0.75
    with pytest.raises(ValueError, match="identity"):
        cache.accept(report().model_copy(update={"device_id": "other"}), now=NOW, monotonic=12)


def test_staleness_uses_monotonic_age_and_queries_do_not_refresh_it():
    cache = Cache("device-test")
    assert cache.accept(report(age=1000), now=NOW, monotonic=10)
    observed = cache.observation(monotonic=30)
    assert observed.status == "stale"
    assert observed.sample.sample_age_ms == observed.age_ms == 21000
    assert observed.last_sample_at.timestamp() == NOW - 1
    assert not cache.accept(report(2, age=20001), now=NOW + 30, monotonic=40)
    assert len(points(cache)) == 1
    compact = cache.observation(monotonic=50, include_snapshot=False)
    assert compact.sample is None and compact.system_info is None and compact.age_ms == 30001
    cache.status = "offline"
    assert cache.observation(monotonic=50).status == "offline"


def test_missing_values_and_waiting_are_distinct_from_zero_and_restart_is_empty():
    cache = Cache("device-test")
    assert cache.accept(report(cpu=None, available=None), now=NOW, monotonic=10)
    assert not points(cache)
    assert cache.accept(report(2, cpu=0.0, available=1000), now=NOW, monotonic=11)
    assert points(cache)[0][2] == 0
    assert not cache.accept(report().model_copy(update={"sample": None}), now=NOW, monotonic=12)
    assert cache.observation(monotonic=12).status == "waiting"
    assert not points(Cache("device-test"))


def test_clock_regression_does_not_overwrite_later_buckets_and_gaps_remain():
    history = MinuteHistory()
    history.add(NOW, 1, (0.1, None, None))
    history.add(NOW + 180, 1, (0.5, None, None))
    history.add(NOW + 60, 1, (0.9, None, None))
    result = history.read(now=NOW + 180, start=NOW, end=NOW + 180)
    data = result.series[0].points
    assert [point[0] for point in data] == [NOW, NOW + 180]
    assert data[0][1] != data[1][1]


def test_downsampling_is_weighted_and_preserves_peaks_gaps_and_segments():
    history = MinuteHistory()
    history.add(NOW, 1, (0.1, None, None))
    history.add(NOW, 1, (0.5, None, None))
    history.add(NOW + 60, 1, (0.9, None, None))
    history.add(NOW + 180, 1, (0.2, None, None))
    history.add(NOW + 240, 2, (0.4, None, None))
    result = history.read(now=NOW + 240, start=NOW, end=NOW + 240, step=300, metric="system_cpu_busy_ratio")
    data = result.series[0].points
    assert len(data) == 3 and data[0][2:] == (0.5, 0.9)
    assert len({point[1] for point in data}) == 3
    assert result.resolution_seconds == 300
    assert not history.read(now=NOW, start=NOW + 0.1, end=NOW + 0.9).series


def network_report(sequence, *, value=100, identity="if-1", name="eth0", generation=1, age=0):
    result = report(sequence, generation=generation, age=age)
    return result.model_copy(
        update={
            "sample": result.sample.model_copy(
                update={
                    "network": NetworkMetrics(
                        interfaces={
                            name: NetworkInterfaceMetrics(
                                identity=identity, received_bytes=value, transmitted_bytes=200
                            )
                        },
                        truncated=False,
                    )
                }
            )
        }
    )


def test_rates_use_monotonic_sample_age_not_wall_clock_and_preserve_zero():
    cache = Cache("device-test")
    cache.accept(network_report(1, age=1000), now=NOW, monotonic=10)
    assert not points(cache, metric="network_received_bytes_per_second")
    cache.accept(network_report(2, value=210, age=0), now=NOW + 60, monotonic=20)
    assert points(cache, now=NOW + 60, metric="network_received_bytes_per_second")[0][2] == 10
    assert points(cache, now=NOW + 60, metric="network_transmitted_bytes_per_second")[0][2] == 0
    result = cache.read(
        MetricQuery(metric="network_received_bytes_per_second", start=NOW, end=NOW + 60, step=60), now=NOW + 60
    )
    assert result.retention_seconds == 3600
    assert result.series[0].labels == {"interface": "eth0", "identity": "if-1"}


@pytest.mark.parametrize(
    "discontinuity", ["missing", "stale", "decrease", "identity", "generation", "offline", "gap", "field"]
)
def test_rate_discontinuities_do_not_create_spikes(discontinuity):
    cache = Cache("device-test")
    cache.accept(network_report(1), now=NOW, monotonic=10)
    cache.accept(network_report(2, value=200), now=NOW + 60, monotonic=20)
    interrupted = network_report(3, value=300)
    if discontinuity == "missing":
        interrupted = report(3)
    elif discontinuity == "stale":
        interrupted = network_report(3, value=300, age=20001)
    elif discontinuity == "decrease":
        interrupted = network_report(3, value=1)
    elif discontinuity == "identity":
        interrupted = network_report(3, value=300, identity="replacement")
    elif discontinuity == "generation":
        interrupted = network_report(3, value=300, generation=2)
    elif discontinuity == "field":
        interrupted = network_report(3, value=None)
    if discontinuity == "offline":
        cache.invalidate_rates()
    cache.accept(interrupted, now=NOW + 120, monotonic=100 if discontinuity == "gap" else 30)
    data = points(cache, now=NOW + 120, metric="network_received_bytes_per_second")
    assert not any(point[0] == NOW + 120 for point in data)
    if discontinuity in {"missing", "stale", "field"}:
        cache.accept(network_report(4, value=400), now=NOW + 180, monotonic=40)
        assert not any(
            point[0] == NOW + 180 for point in points(cache, now=NOW + 180, metric="network_received_bytes_per_second")
        )


def test_resource_churn_is_bounded_and_snapshot_keeps_expanded_inventory():
    cache = Cache("device-test")
    result = network_report(1)
    interfaces = {f"if-{i}": NetworkInterfaceMetrics(identity=str(i), received_bytes=i) for i in range(70)}
    # Deliberately bypass protocol validation to exercise the cache's independent capacity bound.
    result = result.model_copy(
        update={
            "sample": result.sample.model_copy(
                update={"network": NetworkMetrics.model_construct(interfaces=interfaces, truncated=True)}
            )
        }
    )
    cache.accept(result, now=NOW, monotonic=10)
    assert len(cache.resources) == 64
    query = MetricQuery(metric="network_received_bytes_per_second", start=NOW, end=NOW, step=60)
    initial = cache.read(query, now=NOW)
    assert initial.omitted_sources == 6 and initial.inventory_truncated
    for index in range(2, 130):
        cache.accept(network_report(index, name=f"new-{index}"), now=NOW + index, monotonic=10 + index)
        assert len(cache.resources) <= 64
    cache.accept(report(130), now=NOW + 4000, monotonic=4010)
    assert not cache.resources


def test_catalog_projection_matches_scalar_columns_and_rate_expression_is_not_summed():
    from a13n_service.providers.metrics_catalog import scalars

    assert len(scalars(report().sample)) == len(SCALARS)
    assert (
        expression("network_received_bytes_per_second", {"device_id": 'a"b'})
        == 'rate(envd_network_received_bytes_total{device_id="a\\"b"}[5m])'
    )


@pytest.mark.parametrize("step", [60, 300])
def test_partial_counter_disappearance_splits_adjacent_minute_segments(step):
    cache = Cache("device-test")
    for sequence, (offset, value) in enumerate([(40, 100), (50, 200), (60, None), (70, 300), (80, 1300)], 1):
        cache.accept(network_report(sequence, value=value), now=NOW + offset, monotonic=offset)
    data = points(cache, now=NOW + 120, metric="network_received_bytes_per_second", step=step)
    assert [point[2] for point in data] == [10, 100]
    assert data[0][1] != data[1][1]
