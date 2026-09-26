"""Logs and metrics: every record of a request, attempt, sweep pass or delivery names it; lifecycle facts are logged
and counted once their transaction commits; backlog gauges report due work; the executable serves Prometheus, and
the monitoring bundle reads what the Service records."""

import asyncio
import json
import logging
import re
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from a13n_logging.context import ContextFilter
from a13n_service.infra.db import lock, short_session, transaction
from a13n_service.infra.outbox import Claim, Delivery, Policy, Undelivered, enqueue, settle
from a13n_service.infra.sweeps import Sweep, run_sweeps
from a13n_service.runs.backlog import report_backlog
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.seal import stop
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.settings import Settings
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import func, text, update

pytestmark = pytest.mark.anyio

LIFECYCLE = ("Run accepted", "Attempt claimed", "Attempt ended", "Run sealed")
MONITORING = Path(__file__).resolve().parents[3] / "deploy/monitoring"

# A logged record's fields, bound ones included, and its message.
type Event = dict[str, Any]


@pytest.fixture(scope="session")
def reader() -> InMemoryMetricReader:
    """The process's metrics, in memory. A process installs its meter provider once, as the executable does, so
    tests compare values before and after what they measure."""
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    return reader


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Metrics on, so the Harness also records into the process's provider; no test opens the port."""
    return settings.model_copy(update={"telemetry": settings.telemetry.model_copy(update={"metrics_port": 9464})})


class _Events(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.events: list[Event] = []
        # The filter the executable's handlers carry: it adds the fields bound by `log_context`.
        self.addFilter(ContextFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append({**vars(record), "message": record.getMessage()})


@pytest.fixture
def logged() -> Iterator[list[Event]]:
    """What the Service's and Harness's loggers log, however an earlier test configured logging in this process."""
    handler = _Events()
    loggers = [logging.getLogger(name) for name in ("a13n_service", "a13n_harness")]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    yield handler.events
    for logger, level in zip(loggers, levels, strict=True):
        logger.removeHandler(handler)
        logger.setLevel(level)


def measured(reader: InMemoryMetricReader, name: str, attributes: Mapping[str, object] = {}) -> float:
    """A counter's total, a histogram's count or a gauge's value, over the points carrying `attributes`."""
    total = 0.0
    data = reader.get_metrics_data()
    for resource in data.resource_metrics if data is not None else ():
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    if attributes.items() <= dict(point.attributes or {}).items():
                        total += getattr(point, "count", None) or getattr(point, "value", 0)
    return total


async def eventually(check: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not check():
            await asyncio.sleep(0.02)


def messages(events: list[Event], message: str) -> list[Event]:
    return [event for event in events if event["message"] == message]


async def test_requests_are_logged_and_measured_by_route_template_never_by_url(
    service: SimpleNamespace, reader: InMemoryMetricReader, logged: list[Event]
) -> None:
    route = "/api/v1/workspaces/{workspace_id}/agents"
    listed = {"http.route": route, "http.response.status_code": 200}
    before = measured(reader, "http.server.request.duration", listed)

    response = await service.client.get(f"{service.workspace}/agents")
    missing = await service.client.get("/api/v1/nowhere/secret-path?code=secret-code")
    await service.client.get("/healthz")

    finished = messages(logged, "Request finished")
    [ok] = [event for event in finished if event["route"] == route]
    assert (ok["method"], ok["status"], ok["request_id"]) == ("GET", 200, response.headers["x-request-id"])
    [refused] = [event for event in finished if event["status"] == 404]
    assert (refused["route"], refused["request_id"]) == ("unmatched", missing.headers["x-request-id"])
    assert "secret" not in str(refused)
    # Probes are measured, never logged.
    assert "/healthz" not in {event["route"] for event in finished}
    assert measured(reader, "http.server.request.duration", {"http.route": "/healthz"}) >= 1
    assert measured(reader, "http.server.request.duration", listed) == before + 1


async def test_a_run_is_logged_and_counted_from_its_request_to_its_seal(
    executing: SimpleNamespace,
    scripted_model: Any,
    runs_kit: SimpleNamespace,
    reader: InMemoryMetricReader,
    logged: list[Event],
) -> None:
    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("Hello there")
    counted = {
        "a13n.runs.accepted": {"trigger": "input"},
        "a13n.attempt.queue_wait": {},
        "a13n.attempt.duration": {"status": "succeeded"},
        "a13n.runs.sealed": {"status": "completed"},
        "a13n.harness.run.duration": {"a13n.run.outcome": "completed"},
    }
    before = {name: measured(reader, name, attributes) for name, attributes in counted.items()}

    response = await executing.client.post(
        f"{executing.workspace}/threads", json=runs_kit.message(agent, "hi"), headers=runs_kit.fresh_key()
    )
    run_id = response.json()["run"]["id"]
    await runs_kit.sealed(executing, run_id)
    await eventually(lambda: any(event.get("run_id") == run_id for event in messages(logged, "Run sealed")))

    events = [event for event in logged if event["message"] in LIFECYCLE and event.get("run_id") == run_id]
    assert [event["message"] for event in events] == list(LIFECYCLE)
    accepted, claimed, ended, sealed = events
    # The run's acceptance names the request that started it; logs lead from one ID to the other.
    assert (accepted["request_id"], accepted["trigger"]) == (response.headers["x-request-id"], "input")
    assert claimed["queue_wait_ms"] >= 0
    assert (ended["status"], ended["attempt_id"]) == ("succeeded", claimed["attempt_id"])
    # Bound by the worker for everything its attempt logs, not passed by the call.
    assert ended["worker_id"].startswith("wrk_")
    assert (sealed["status"], sealed["reason"]) == ("completed", None)

    for name, attributes in counted.items():
        assert measured(reader, name, attributes) == before[name] + 1, name
    assert measured(reader, "gen_ai.client.token.usage") > 0
    slots = executing.runtime.settings.worker.slots
    await eventually(lambda: measured(reader, "a13n.worker.slots", {"state": "free"}) == slots)


async def test_a_seal_that_rolls_back_is_never_counted_or_logged(
    service: SimpleNamespace,
    scripted_model: Any,
    runs_kit: SimpleNamespace,
    reader: InMemoryMetricReader,
    logged: list[Event],
) -> None:
    cancelled = {"status": "cancelled", "reason": "cancelled"}
    before = measured(reader, "a13n.runs.sealed", cancelled)
    agent = await runs_kit.create_agent(service, scripted_model)
    run = (await runs_kit.start_thread(service, agent, "hi"))["run"]
    runtime: Runtime = service.runtime

    with pytest.raises(RuntimeError):
        async with transaction(runtime.storage) as session:
            thread = await lock(session, ThreadRow, run["thread_id"])
            row = await lock(session, RunRow, run["id"])
            assert thread is not None and row is not None
            await stop(session, runtime, thread, row)
            raise RuntimeError("the transaction rolls back")
    assert measured(reader, "a13n.runs.sealed", cancelled) == before
    assert not messages(logged, "Run sealed")

    response = await service.client.post(f"{service.workspace}/runs/{run['id']}/interrupt")
    assert response.status_code == 200, response.text
    assert measured(reader, "a13n.runs.sealed", cancelled) == before + 1
    [sealed] = messages(logged, "Run sealed")
    assert (sealed["run_id"], sealed["reason"]) == (run["id"], "cancelled")


async def test_backlog_reports_due_runs_but_not_later_ones(
    service: SimpleNamespace,
    scripted_model: Any,
    runs_kit: SimpleNamespace,
    reader: InMemoryMetricReader,
) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    due = (await runs_kit.start_thread(service, agent, "now"))["run"]
    later = (await runs_kit.start_thread(service, agent, "later"))["run"]
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(RunRow).where(RunRow.id == due["id"]).values(available_at=func.now() - timedelta(minutes=5))
        )
        await session.execute(
            update(RunRow).where(RunRow.id == later["id"]).values(available_at=func.now() + timedelta(minutes=5))
        )

    await report_backlog(service.runtime.storage)

    assert measured(reader, "a13n.backlog.size", {"queue": "runs"}) == 1
    assert 300 <= measured(reader, "a13n.backlog.oldest_age", {"queue": "runs"}) < 360
    assert measured(reader, "a13n.backlog.size", {"queue": "webhook"}) == 0
    assert measured(reader, "a13n.backlog.oldest_age", {"queue": "webhook"}) == 0


async def test_deliveries_are_counted_and_logged_by_outcome(
    runtime: Runtime, reader: InMemoryMetricReader, logged: list[Event]
) -> None:
    async with transaction(runtime.storage) as session:
        for purpose in ("delivered", "dead"):
            enqueue(
                session, organization_id=None, workspace_id=None, kind="email", target={"purpose": purpose}, payload={}
            )
    outcome = {result: {"kind": "email", "result": result} for result in ("delivered", "dead")}
    before = {result: measured(reader, "a13n.outbox.deliveries", labels) for result, labels in outcome.items()}

    async def handle(claimed: Claim) -> None:
        logging.getLogger("a13n_service.test").info("Handling")
        if claimed.target["purpose"] == "dead":
            raise Undelivered("smtp_down")
        async with transaction(runtime.storage) as session:
            await settle(session, claimed, "delivered")

    await Delivery(
        runtime.storage, {"email": handle}, owner="test", policies={"email": Policy(batch=10, max_attempts=1)}
    )()

    for result, labels in outcome.items():
        assert measured(reader, "a13n.outbox.deliveries", labels) == before[result] + 1
    [delivered] = messages(logged, "Outbox delivered")
    [dead] = messages(logged, "Outbox delivery dead")
    assert (dead["kind"], dead["reason"]) == ("email", "smtp_down")
    # A handler's own records name the delivery it handles.
    assert {event["outbox_id"] for event in messages(logged, "Handling")} == {delivered["outbox_id"], dead["outbox_id"]}


async def test_sweep_passes_are_counted_and_their_records_name_the_sweep(
    reader: InMemoryMetricReader, logged: list[Event]
) -> None:
    passes = {result: {"sweep": "flaky", "result": result} for result in ("failed", "succeeded")}
    before = {result: measured(reader, "a13n.sweep.passes", labels) for result, labels in passes.items()}
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first pass fails")

    task = asyncio.create_task(run_sweeps([Sweep(name="flaky", every=0.01, run=flaky, timeout=1)]))
    try:
        await eventually(lambda: measured(reader, "a13n.sweep.passes", passes["succeeded"]) > before["succeeded"])
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert measured(reader, "a13n.sweep.passes", passes["failed"]) == before["failed"] + 1
    [failure] = messages(logged, "Sweep failed")
    assert (failure["sweep"], failure["error_type"]) == ("flaky", "RuntimeError")
    assert failure["exception_details"][0]["frames"][-1]["function"] == "flaky"
    assert "first pass fails" not in json.dumps(failure)


def test_the_executable_serves_metrics_in_the_prometheus_format() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # One measurement of every Service instrument.
    script = f"""
import sys
from a13n_service.infra.telemetry import serve_metrics
serve_metrics("127.0.0.1", {port})
from a13n_service.infra.http import REQUEST_DURATION
from a13n_service.infra.outbox import DELIVERIES
from a13n_service.infra.sweeps import PASSES
from a13n_service.runs.accept import RUNS_ACCEPTED
from a13n_service.runs.backlog import BACKLOG_OLDEST_AGE, BACKLOG_SIZE
from a13n_service.runs.claim import QUEUE_WAIT
from a13n_service.runs.seal import ATTEMPT_DURATION, RUNS_SEALED
from a13n_service.runs.worker import SLOTS
labels = {{"http.request.method": "GET", "http.route": "/healthz", "http.response.status_code": 200}}
REQUEST_DURATION.record(0.01, labels)
RUNS_ACCEPTED.add(1, {{"trigger": "input"}})
RUNS_SEALED.add(1, {{"status": "failed", "reason": "lease_expired"}})
QUEUE_WAIT.record(1)
ATTEMPT_DURATION.record(42, {{"status": "failed"}})
SLOTS.set(4, {{"state": "free"}})
BACKLOG_SIZE.set(3, {{"queue": "runs"}})
BACKLOG_OLDEST_AGE.set(5, {{"queue": "runs"}})
DELIVERIES.add(1, {{"kind": "email", "result": "dead"}})
PASSES.add(1, {{"sweep": "expire_leases", "result": "failed"}})
print("ready", flush=True)
sys.stdin.read()
"""
    process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == "ready"
        with httpx2.Client(trust_env=False) as client:
            first, second = (client.get(f"http://127.0.0.1:{port}/metrics").text for _ in range(2))
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=10)

    assert 'a13n_runs_sealed_total{reason="lease_expired",status="failed"} 1.0' in first
    # Buckets sized for seconds, not the OpenTelemetry defaults sized for milliseconds.
    assert 'a13n_attempt_duration_seconds_bucket{le="30",status="failed"} 0.0' in first
    assert 'a13n_attempt_duration_seconds_bucket{le="60",status="failed"} 1.0' in first
    # A gauge keeps its value until it is replaced, however often it is scraped.
    assert 'a13n_backlog_size{queue="runs"} 3.0' in first
    assert 'a13n_backlog_size{queue="runs"} 3.0' in second
    assert 'service_name="a13n-service"' in first
    assert "otel_scope" not in first
    # The alert rules and the operations dashboard select only series the Service serves.
    selected = set()
    for name in ("alerts.yaml", "operations-dashboard.json"):
        selected |= set(re.findall(r"\b(?:a13n|http_server)_[a-z_]+", (MONITORING / name).read_text()))
    assert selected
    served = set(re.findall(r"^([a-z0-9_]+)[{ ]", first, re.MULTILINE))
    assert selected <= served, selected - served


async def test_the_usage_dashboard_reads_the_facts_of_a_run(
    executing: SimpleNamespace, scripted_model: Any, runs_kit: SimpleNamespace
) -> None:
    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("Hello there")
    run = (await runs_kit.start_thread(executing, agent, "hi"))["run"]
    await runs_kit.sealed(executing, run["id"])

    dashboard = json.loads((MONITORING / "usage-dashboard.json").read_text())
    queries = {panel["title"]: target["rawSql"] for panel in dashboard["panels"] for target in panel.get("targets", ())}
    assert queries
    async with short_session(executing.runtime.storage) as session:
        for title, query in queries.items():
            # Grafana expands the time filter to the dashboard's range.
            expanded = re.sub(r"\$__timeFilter\(([^)]+)\)", r"\1 > now() - interval '1 day'", query)
            rows = (await session.execute(text(expanded))).all()
            assert rows or title == "Failed runs by reason", title


async def test_after_commit_closes_its_session_and_logs_stack_locations_without_secrets(
    runtime, tenant, logged
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.infra.db import after_commit
    from a13n_service.tenancy.tables import PrincipalRow
    from sqlalchemy import inspect

    async def fail() -> None:
        assert inspect(principal).detached
        raise RuntimeError("credential-that-must-not-be-logged")

    async with transaction(runtime.storage) as session:
        principal = await session.get_one(PrincipalRow, tenant.principal_id)
        after_commit(session, fail)
    [failure] = messages(logged, "After-commit callback failed")
    assert failure["exception_details"][0]["frames"][-1]["function"] == "fail"
    assert failure["error_type"] == "RuntimeError"
    assert "credential-that-must-not-be-logged" not in json.dumps(failure)

    with pytest.raises(ValueError):
        async with transaction(runtime.storage) as session:
            after_commit(session, fail)
            raise ValueError("rollback")
    assert len(messages(logged, "After-commit callback failed")) == 1
