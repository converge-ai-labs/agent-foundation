"""Offline accounting proofs: raw outcomes, deliberate waits, cancellation and failed cases."""

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from ..control.contention_support import (
    BARRIER_INCLUSIVE,
    RELEASE_OBSERVATION,
    ContentionMetrics,
    contention_case,
    contention_metrics,
    measured_call,
    prepared_writers,
)

pytestmark = pytest.mark.anyio


async def test_success_conflict_and_error_have_separate_raw_samples_and_percentiles(tmp_path):
    now = [0.0]
    metrics = ContentionMetrics(tmp_path, "queue", "Eight queue writers", {}, clock=lambda: now[0])

    async def response(duration_ms, status):
        now[0] += duration_ms / 1000
        return SimpleNamespace(status_code=status)

    for duration in range(1, 21):
        await metrics.call("enqueue", f"writer-{duration}", lambda duration=duration: response(duration, 202))
    await metrics.call("enqueue", "capacity loser", lambda: response(80, 409), expected_statuses=(409,))
    await metrics.call("enqueue", "server failure", lambda: response(100, 500), expected_statuses=(409,))
    await metrics.call("enqueue", "unexpected conflict", lambda: response(60, 409))
    report = metrics.save("failed", "AssertionError")
    groups = {group["outcome"]: group for group in report["groups"]}
    assert {key: value["n"] for key, value in groups.items()} == {"success": 20, "expected_conflict": 1, "error": 2}
    assert [groups["success"][f"p{q}_ms"] for q in (50, 95, 99)] == pytest.approx([10, 19, 20])
    assert groups["expected_conflict"]["p99_ms"] == pytest.approx(80)
    assert groups["error"]["p50_ms"] == pytest.approx(60)
    assert all(group["performance_verdict"] == "diagnostic_only" for group in groups.values())
    raw = [json.loads(line) for line in (metrics.directory / "samples.jsonl").read_text().splitlines()]
    assert raw == metrics.samples and len(raw) == report["sample_count"] == 23
    assert json.loads((metrics.directory / "summary.json").read_text()) == report


async def test_expected_authority_rejection_and_unexpected_exception_are_not_success(tmp_path):
    metrics = ContentionMetrics(tmp_path, "authority", "Heartbeat versus cancellation", {})

    async def reject(error):
        raise error

    with pytest.raises(ValueError):
        await metrics.call(
            "heartbeat", "stale authority", lambda: reject(ValueError()), expected_exceptions=(ValueError,)
        )
    with pytest.raises(RuntimeError):
        await metrics.call(
            "heartbeat", "database failure", lambda: reject(RuntimeError()), expected_exceptions=(ValueError,)
        )
    assert [sample["outcome"] for sample in metrics.samples] == ["expected_conflict", "error"]
    assert [sample["error_type"] for sample in metrics.samples] == ["ValueError", "RuntimeError"]


async def test_wait_inclusive_and_release_observation_are_explicitly_distinct(tmp_path):
    now = [0.0]
    metrics = ContentionMetrics(tmp_path, "claim", "Four schedulers contend for one Run", {}, clock=lambda: now[0])
    with metrics.measure("request_barrier_inclusive", "HTTP writer", semantics=BARRIER_INCLUSIVE):
        now[0] = 5.0  # Deliberate test coordination, not service latency.
        with metrics.measure("claim_release_to_model", "One winning worker", semantics=RELEASE_OBSERVATION):
            now[0] += 0.02
    report = metrics.save("passed")
    groups = {group["operation"]: group for group in report["groups"]}
    assert groups["request_barrier_inclusive"]["p50_ms"] == pytest.approx(5020)
    winner = groups["claim_release_to_model"]
    assert [winner[f"p{q}_ms"] for q in (50, 95, 99)] == pytest.approx([20, 20, 20])
    assert winner["n"] == 1 and winner["performance_verdict"] == "diagnostic_only"
    assert "not service latency" in groups["request_barrier_inclusive"]["timing_semantics"]
    assert "excluding the deliberate hold" in winner["timing_semantics"]


@pytest.mark.parametrize("cancel", [False, True], ids=["assertion-failure", "cancellation"])
async def test_failed_case_flushes_joined_contenders_and_preserves_fixture_signature(tmp_path, cancel):
    entered = asyncio.Event()
    release = asyncio.Event()

    class Journey:
        lab = SimpleNamespace(root=tmp_path)

        def __init__(self):
            self.live = SimpleNamespace(http=self)

        def arm(self, name, point, **match):
            barrier = tmp_path / name
            barrier.mkdir()
            return barrier

        async def post(self, path, **kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(status_code=202)

        async def reached(self, barrier, **kwargs):
            await entered.wait()
            return {"run_id": "run-1"}

        def release(self, barrier):
            release.set()

    @contention_case("{writers} writers interrupted during the prepared barrier")
    async def exercise(control, writers):
        async with prepared_writers(
            control,
            [("/runs", {}, "key")],
            point="prepared",
            operation="accept_barrier_inclusive",
            actor="Admission writer",
        ):
            if cancel:
                await asyncio.Event().wait()
            raise AssertionError("deliberate correctness failure")

    assert list(inspect.signature(exercise).parameters) == ["control", "writers"]
    task = asyncio.create_task(exercise(Journey(), 1))
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else AssertionError):
        await task
    (summary_path,) = tmp_path.glob("contention-metrics/*/summary.json")
    report = json.loads(summary_path.read_text())
    assert report["case_outcome"] == "failed"
    assert report["case_error_type"] == ("CancelledError" if cancel else "AssertionError")
    assert report["parameters"] == {"writers": 1} and "1 writers" in report["scenario"]
    assert report["sample_count"] == 1
    (sample,) = [json.loads(line) for line in summary_path.with_name("samples.jsonl").read_text().splitlines()]
    assert sample["outcome"] == "error" and sample["error_type"] == "CancelledError"
    assert release.is_set()
    with pytest.raises(AssertionError, match="must use"):
        contention_metrics()


async def test_shared_helper_is_unmodified_without_a_contention_recorder():
    response = SimpleNamespace(status_code=409)

    async def action():
        return response

    assert await measured_call("test", "test actor", action) is response
