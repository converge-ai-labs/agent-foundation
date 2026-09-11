"""Measurement boundaries, concurrency, failure evidence and opt-in profiles."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from ..performance import operations
from ..performance.operation_config import OperationConfig, read_config
from ..performance.operations import Benchmark, Operation, distribution, wave


async def verified(result):
    assert result == "ok"


@pytest.mark.anyio
async def test_preparation_verification_cleanup_and_warmup_are_outside_samples(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(operations, "perf_counter", lambda: clock[0])
    calls = []

    @asynccontextmanager
    async def prepare(count):
        clock[0] += 100

        async def call():
            calls.append("call")
            clock[0] += 0.125
            return "ok"

        async def verify(result):
            assert result == "ok"
            clock[0] += 100

        yield [Operation(call, verify) for _ in range(count)]
        clock[0] += 100

    benchmark = Benchmark(tmp_path / "report.json", samples=2, warmup=1, environment={})
    await benchmark.measure("steer.append", 1, prepare, configuration={}, boundary="test")
    benchmark.finish()
    cell = benchmark.report["cells"][0]
    assert len(calls) == 3 and cell["samples_ms"]["success"] == [125, 125]
    assert cell["summary"]["success"]["n"] == 2
    assert distribution([])["p99_ms"] is None


@pytest.mark.anyio
async def test_wave_releases_all_callers_and_stops_all_timers_before_verification():
    entered, finished, verified_count = [], [], []
    release = asyncio.Event()

    async def call():
        entered.append(True)
        if len(entered) == 256:
            release.set()
        await release.wait()
        assert not verified_count
        finished.append(True)
        return "ok"

    async def verify(result):
        assert len(finished) == 256 and result == "ok"
        verified_count.append(True)

    samples = []
    peak = await wave([Operation(call, verify) for _ in range(256)], samples)
    assert peak == 256 and len(samples) == len(verified_count) == 256


@pytest.mark.anyio
async def test_preparation_releases_all_256_callers_before_waiting_for_results():
    from ..performance.service_fixtures import prepare_many

    active, created = 0, []
    release = asyncio.Event()

    async def create():
        nonlocal active
        active += 1
        try:
            value = len(created)
            created.append(value)
            if active == 256:
                release.set()
            await release.wait()
            return value
        finally:
            active -= 1

    async with asyncio.timeout(5):
        assert await prepare_many(create, 256) == list(range(256))
    assert active == 0


@pytest.mark.anyio
async def test_failed_preparation_drains_other_callers_before_returning():
    from ..performance.service_fixtures import prepare_many

    started, active = 0, 0
    ready = asyncio.Event()

    async def create():
        nonlocal started, active
        index = started
        started += 1
        active += 1
        try:
            if started == 8:
                ready.set()
            await ready.wait()
            if index == 0:
                raise ValueError("fixture failed")
            await asyncio.Event().wait()
        finally:
            active -= 1

    async with asyncio.timeout(5):
        with pytest.raises(ExceptionGroup, match="TaskGroup"):
            await prepare_many(create, 8)
    assert active == 0 and started == 8


@pytest.mark.anyio
async def test_cancellation_retains_finished_and_inflight_samples_before_cleanup(tmp_path):
    ready = asyncio.Event()
    closed = []

    async def fast():
        return "ok"

    async def blocked():
        ready.set()
        await asyncio.Event().wait()

    @asynccontextmanager
    async def prepare(count):
        try:
            yield [Operation(fast, verified), Operation(blocked, verified)]
        finally:
            closed.append(True)

    benchmark = Benchmark(tmp_path / "report.json", samples=2, warmup=0, environment={})
    task = asyncio.create_task(benchmark.measure("steer.append", 2, prepare, configuration={}, boundary="test"))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    persisted = json.loads(benchmark.path.read_text())
    cell = persisted["cells"][0]
    assert closed and persisted["status"] == cell["status"] == "failed"
    assert cell["summary"]["success"]["n"] == cell["summary"]["cancelled"]["n"] == 1


@pytest.mark.anyio
async def test_stalled_driver_cancellation_fails_with_partial_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(operations, "OPERATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(operations, "CANCELLATION_GRACE_SECONDS", 0.01)
    closed = []

    async def stalled():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Mimic a driver waiting for the server's cancellation response.
            await asyncio.Event().wait()

    @asynccontextmanager
    async def prepare(count):
        try:
            yield [Operation(stalled, verified)]
        finally:
            closed.append(True)

    benchmark = Benchmark(tmp_path / "report.json", samples=1, warmup=0, environment={})
    task = asyncio.create_task(benchmark.measure("pg.update", 1, prepare, configuration={}, boundary="test"))
    done, pending = await asyncio.wait({task}, timeout=1)
    if pending:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task in done, "The wave must finish within its own cancellation deadline"
    with pytest.raises(TimeoutError):
        await task
    cell = json.loads(benchmark.path.read_text())["cells"][0]
    assert closed and cell["status"] == "failed"
    assert cell["summary"]["cancelled"]["n"] == 1


@pytest.mark.anyio
async def test_expected_exception_still_requires_correct_persisted_outcome(tmp_path):
    async def denied():
        raise ValueError("wrong rejection reason")

    @asynccontextmanager
    async def prepare(count):
        yield [Operation(denied, verified, ValueError)]

    benchmark = Benchmark(tmp_path / "report.json", samples=1, warmup=0, environment={})
    with pytest.raises(AssertionError):
        await benchmark.measure("queue.enqueue_full", 1, prepare, configuration={}, boundary="test")
    cell = benchmark.report["cells"][0]
    assert cell["summary"]["verification_error"]["n"] == 1
    assert cell["summary"]["expected_conflict"]["n"] == 0


@pytest.mark.anyio
async def test_eligible_budget_violation_fails_the_benchmark(tmp_path):
    @asynccontextmanager
    async def prepare(count):
        async def call():
            return "ok"

        yield [Operation(call, verified)]

    benchmark = Benchmark(
        tmp_path / "report.json", samples=100, warmup=0, environment={}, budgets={"steer.append": {"p95": 0.0000001}}
    )
    await benchmark.measure("steer.append", 1, prepare, configuration={}, boundary="test")
    with pytest.raises(AssertionError, match="latency budget"):
        benchmark.finish()
    assert json.loads(benchmark.path.read_text())["status"] == "failed"


@pytest.mark.parametrize(
    "values",
    [
        {"scenarios": []},
        {"scenarios": ["unknown"]},
        {"scenarios": ["pg.update", "pg.update"]},
        {"concurrency": []},
        {"concurrency": [1, 1]},
        {"concurrency": [257]},
        {"concurrency": [8], "samples": 4},
        {"samples": 128},
        {"pg_pool_size": 1},
        {"pg_pool_size": 257},
        {"s3_pool_size": 257},
        {"http_pool_size": 128},
        {"thread_connection_states": []},
        {"thread_connection_states": ["cold", "cold"]},
        {"thread_connection_states": ["unknown"]},
        {"payload_bytes": [0]},
        {"payload_bytes": [1, 1]},
        {"samples": True},
        {"budgets_ms": {"typo": {"p95": 10}}},
        {"budgets_ms": {"s3.put": {"p50": 10}}},
        {"budgets_ms": {"s3.put": {"p95": float("inf")}}},
    ],
)
def test_invalid_profiles_fail_before_infrastructure(values):
    with pytest.raises(ValidationError):
        OperationConfig.model_validate(values)


def test_example_profile_is_the_default_matrix():
    from pathlib import Path

    assert read_config(Path(__file__).parents[1] / "performance/operations.example.toml") == OperationConfig()
