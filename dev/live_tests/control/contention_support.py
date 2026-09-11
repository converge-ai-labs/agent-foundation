"""Bounded HTTP contenders and read-only evidence from the fixture-owned PostgreSQL."""

import asyncio
import inspect
import json
import logging
import math
import time
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


BARRIER_INCLUSIVE = (
    "Request/call start to completion, including deliberate test barriers or held database locks; "
    "diagnostic wall time, not service latency."
)
RELEASE_OBSERVATION = (
    "Barrier release to observed milestone, excluding the deliberate hold before release; "
    "includes observer polling and scheduling, not an internal service timer."
)
_current_metrics = ContextVar("contention_metrics", default=None)


class ContentionMetrics:
    """Account in memory; persist after the case, even on failure.

    Nearest-rank percentiles are diagnostic only, including n=1 observations.
    Artifact I/O never runs inside a measured call.
    """

    def __init__(self, root, case, scenario, parameters, *, clock=time.perf_counter):
        self.directory = root / "contention-metrics" / (case + "-" + uuid4().hex)
        self.case, self.scenario, self.parameters = case, scenario, parameters
        self.clock = clock
        self.samples = []

    @contextmanager
    def measure(self, operation, actor, *, semantics, expected_exceptions=()):
        sample = {"operation": operation, "actor": actor, "timing_semantics": semantics, "outcome": "success"}
        started = self.clock()
        try:
            yield sample
        except BaseException as error:
            sample["outcome"] = "expected_conflict" if isinstance(error, expected_exceptions) else "error"
            sample["error_type"] = type(error).__name__
            raise
        finally:
            sample["duration_ms"] = max(0.0, (self.clock() - started) * 1000)
            self.samples.append(sample)

    async def call(
        self, operation, actor, action, *, semantics=BARRIER_INCLUSIVE, expected_statuses=(), expected_exceptions=()
    ):
        with self.measure(operation, actor, semantics=semantics, expected_exceptions=expected_exceptions) as sample:
            result = await action()
            if hasattr(result, "status_code"):
                sample["status_code"] = result.status_code
                if result.status_code in expected_statuses:
                    sample["outcome"] = "expected_conflict"
                elif not 200 <= result.status_code < 300:
                    sample["outcome"] = "error"
            return result

    def save(self, outcome, error_type=None):
        groups = defaultdict(list)
        for sample in self.samples:
            groups[(sample["operation"], sample["outcome"], sample["timing_semantics"])].append(sample)
        summaries = []
        for (operation, result, semantics), samples in sorted(groups.items()):
            values = sorted(sample["duration_ms"] for sample in samples)
            summaries.append(
                {
                    "operation": operation,
                    "outcome": result,
                    "timing_semantics": semantics,
                    "actors": sorted({sample["actor"] for sample in samples}),
                    "n": len(values),
                    **{f"p{q}_ms": values[math.ceil(len(values) * q / 100) - 1] for q in (50, 95, 99)},
                    "max_ms": values[-1],
                    "performance_verdict": "diagnostic_only",
                }
            )
        report = {
            "schema_version": 1,
            "case": self.case,
            "scenario": self.scenario,
            "parameters": self.parameters,
            "case_outcome": outcome,
            "case_error_type": error_type,
            "percentile_method": "nearest_rank",
            "performance_verdict": "diagnostic_only",
            "sample_count": len(self.samples),
            "groups": summaries,
            "raw_samples_file": "samples.jsonl",
            "scope": "Competing mutations and named claim/recovery milestones; excludes setup and verification calls.",
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "samples.jsonl").write_text("".join(json.dumps(sample) + "\n" for sample in self.samples))
        temporary = self.directory / "summary.json.tmp"
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(self.directory / "summary.json")
        logger.info(
            "Contention timings: scenario=%s samples=%s artifact=%s", self.scenario, len(self.samples), self.directory
        )
        return report


def contention_case(scenario):
    """Preserve pytest fixture signature and flush after task cleanup on every exit."""

    def decorate(function):
        @wraps(function)
        async def measured(*args, **kwargs):
            arguments = inspect.signature(function).bind(*args, **kwargs).arguments
            parameters = {key: value for key, value in arguments.items() if isinstance(value, (str, int, float, bool))}
            metrics = ContentionMetrics(
                arguments["control"].lab.root, function.__name__, scenario.format(**parameters), parameters
            )
            token = _current_metrics.set(metrics)
            outcome, error_type = "passed", None
            try:
                return await function(*args, **kwargs)
            except BaseException as error:
                outcome, error_type = "failed", type(error).__name__
                raise
            finally:
                _current_metrics.reset(token)
                metrics.save(outcome, error_type)

        return measured

    return decorate


def contention_metrics():
    metrics = _current_metrics.get()
    assert metrics is not None, "Contention test must use @contention_case"
    return metrics


async def measured_call(operation, actor, action, *, expected_statuses=()):
    # Shared ControlJourney helpers retain existing behavior in other tests.
    metrics = _current_metrics.get()
    if metrics is None or operation is None:
        return await action()
    return await metrics.call(operation, actor, action, expected_statuses=expected_statuses)


@asynccontextmanager
async def prepared_writers(journey, commands, *, point, operation, actor, expected_statuses=(409,), **match):
    """Every request reaches the real operation before any contender is released."""
    barrier = journey.arm("writers-" + uuid4().hex, point, role="control", times=len(commands), **match)
    tasks = [
        asyncio.create_task(
            measured_call(
                operation,
                f"{actor} {index + 1}/{len(commands)}",
                lambda path=path, body=body, key=key: journey.live.http.post(
                    path, json=body, headers={"Idempotency-Key": key}
                ),
                expected_statuses=expected_statuses,
            )
        )
        for index, (path, body, key) in enumerate(commands)
    ]
    try:
        hits = [await journey.reached(barrier, hit=index) for index in range(1, len(tasks) + 1)]
        assert all(not task.done() for task in tasks)
        assert not list(barrier.glob("finished-*.json")), "A contender timed out before release"
        logger.info("Prepared contention: point=%s writers=%s", point, len(tasks))
        yield barrier, tasks, hits
    finally:
        journey.release(barrier)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def query(journey, statement, parameters):
    # This independent observer opens only the lab's disposable database. It
    # does not construct a Service session factory or mutate lifecycle records.
    url = journey.lab.environment["A13N_SERVICE_DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:", 1)
    async with await psycopg.AsyncConnection.connect(
        url, row_factory=dict_row, options="-c default_transaction_read_only=on -c statement_timeout=3000"
    ) as database:
        cursor = await database.execute(statement, parameters)
        return await cursor.fetchall()


async def lifecycle(journey, run_ids):
    return await query(
        journey,
        "SELECT id, entity_type, entity_id, resource_seq, event_type, mutation_id, run_id "
        "FROM lifecycle_events WHERE organization_id = %s AND run_id = ANY(%s) ORDER BY seq",
        (journey.live.config["organization_id"], list(run_ids)),
    )


def assert_lifecycle(events, run_id, outcome, attempts):
    """No duplicate facts; each Run terminal fact shares its Attempt mutation."""
    events = [event for event in events if event["run_id"] == run_id]
    groups = defaultdict(list)
    for event in events:
        groups[event["entity_id"]].append(event)
    run_events = groups[run_id]
    assert [event["event_type"] for event in run_events] == ["run.accepted", "run.running", "run." + outcome]
    assert set(groups) == {run_id, *(attempt["id"] for attempt in attempts)}
    for group in groups.values():
        assert [event["resource_seq"] for event in group] == list(range(1, len(group) + 1))
        assert len({event["id"] for event in group}) == len(group)
    for attempt in attempts:
        facts = groups[attempt["id"]]
        assert facts[0]["event_type"] == "run_attempt.leased"
        assert facts[-1]["event_type"] == "run_attempt." + attempt["status"]
        assert sum(event["event_type"] == "run_attempt.leased" for event in facts) == 1
    terminal = groups[attempts[-1]["id"]][-1]
    assert terminal["mutation_id"] == run_events[-1]["mutation_id"]


async def inbox_budget(journey, thread_id):
    rows = await query(
        journey,
        "SELECT next_delivery_sequence, pending_count, pending_bytes FROM thread_inbox_counters "
        "WHERE organization_id = %s AND thread_id = %s",
        (journey.live.config["organization_id"], thread_id),
    )
    assert len(rows) == 1
    return rows[0]
