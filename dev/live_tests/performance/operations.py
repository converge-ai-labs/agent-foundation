"""Bounded operation sampling; preparation and verification never enter the clock."""

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)
OPERATION_TIMEOUT_SECONDS = 60
CANCELLATION_GRACE_SECONDS = 5


@dataclass
class Operation:
    call: Callable[[], Awaitable[Any]]
    verify: Callable[[Any], Awaitable[None]]
    expected_error: type[Exception] | None = None
    allow_success: bool = False


def distribution(values):
    ordered = sorted(values)
    return {
        "n": len(ordered),
        **{f"p{p}_ms": ordered[math.ceil(len(ordered) * p / 100) - 1] if ordered else None for p in (50, 95, 99)},
    }


@dataclass
class Sample:
    operation: Operation
    outcome: str
    elapsed_ms: float
    result: Any


async def wave(operations, observations):
    """Keep partial samples on cancellation and verify only after all clocks stop."""
    start = asyncio.Event()
    active, peak = 0, 0

    async def invoke(operation):
        nonlocal active, peak
        await start.wait()
        active += 1
        peak = max(peak, active)
        started = perf_counter()
        outcome, result = "success", None
        try:
            async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
                result = await operation.call()
        except asyncio.CancelledError as error:
            outcome, result = "cancelled", error
            raise
        except Exception as error:
            outcome = "expected_conflict" if isinstance(error, operation.expected_error or ()) else "error"
            result = error
        finally:
            elapsed = (perf_counter() - started) * 1000
            observations.append(Sample(operation, outcome, elapsed, result))
            active -= 1

    tasks = [asyncio.create_task(invoke(operation)) for operation in operations]
    start.set()
    try:
        # A driver may await server cancellation after its call is cancelled.
        # A second cancellation bounds that grace period before fixture cleanup.
        async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS + CANCELLATION_GRACE_SECONDS):
            await asyncio.gather(*tasks)
    finally:
        # Finish cancellation before a preparation context closes clients/connections.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for sample in observations:
        if sample.outcome in {"error", "cancelled"}:
            continue
        try:
            operation = sample.operation
            if operation.expected_error is not None and not operation.allow_success:
                assert sample.outcome == "expected_conflict", "Expected rejection unexpectedly succeeded"
            await operation.verify(sample.result)
        except Exception as error:
            sample.outcome, sample.result = "verification_error", error
    return peak


class Benchmark:
    def __init__(self, path, *, samples, warmup, environment, budgets=None):
        self.path, self.samples, self.warmup = path, samples, warmup
        self.budgets = budgets or {}
        self.report = {
            "schema": "bounded-operations-v1",
            "status": "incomplete",
            "environment": environment,
            "cells": [],
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.report, indent=2) + "\n")

    async def measure(self, scenario, concurrency, prepare, *, configuration, boundary):
        cell = {
            "scenario": scenario,
            "concurrency": concurrency,
            "configuration": configuration,
            "boundary": boundary,
            "warmup_waves": self.warmup,
            "status": "incomplete",
            "samples_ms": {
                name: [] for name in ("success", "expected_conflict", "error", "verification_error", "cancelled")
            },
            "errors": [],
            "budgets_ms": self.budgets.get(scenario, {}),
            "peak_inflight": 0,
            "measured_wave_sizes": [],
        }
        self.report["cells"].append(cell)
        self.save()
        try:
            for index in range(self.warmup + math.ceil(self.samples / concurrency)):
                measured = index >= self.warmup
                count = (
                    min(concurrency, self.samples - (index - self.warmup) * concurrency) if measured else concurrency
                )
                observations = []
                async with prepare(count) as operations:
                    try:
                        peak = await wave(operations, observations)
                        if measured:
                            cell["peak_inflight"] = max(cell["peak_inflight"], peak)
                            cell["measured_wave_sizes"].append(count)
                    finally:
                        for sample in observations:
                            if measured:
                                cell["samples_ms"][sample.outcome].append(sample.elapsed_ms)
                            if sample.outcome in {"error", "verification_error", "cancelled"}:
                                cell["errors"].append(type(sample.result).__name__)
                                logger.error(
                                    "operation failed: %s",
                                    scenario,
                                    exc_info=(type(sample.result), sample.result, sample.result.__traceback__),
                                )
                if cell["errors"]:
                    raise AssertionError(f"{scenario}: {cell['errors']}")
            cell["status"] = "verified"
        except BaseException as error:
            cell["status"] = "failed"
            cell["errors"].append(type(error).__name__)
            self.report["status"] = "failed"
            raise
        finally:
            cell["summary"] = {name: distribution(values) for name, values in cell["samples_ms"].items()}
            self.save()
            logger.info(
                "operation_benchmark scenario=%s concurrency=%s status=%s report=%s",
                scenario,
                concurrency,
                cell["status"],
                self.path,
            )

    def finish(self):
        if not self.report["cells"]:
            self.report["status"] = "failed"
            self.save()
            raise AssertionError("No selected scenarios apply to this benchmark target")
        self.report["status"] = "verified"
        self.save()
        from .scenario_report_rows import rows

        failures = [row.scenario for row in rows(self.report) if row.result.startswith("Fail:")]
        if failures:
            self.report["status"] = "failed"
            self.save()
            raise AssertionError("Operation latency budget failed: " + ", ".join(failures))
