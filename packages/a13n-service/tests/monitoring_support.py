"""Inputs shared by the monitoring bundle's SQL, exporter and live interoperability tests."""

import json
from pathlib import Path

MONITORING = Path(__file__).resolve().parents[3] / "deploy/monitoring"


def dashboard(name):
    return json.loads((MONITORING / f"{name}-dashboard.json").read_text())


def usage_queries():
    return {
        panel["title"]: target["rawSql"]
        for panel in dashboard("usage")["panels"]
        for target in panel.get("targets", ())
    }


def expand_usage(query, *, organizations, workspaces, start, end):
    """Grafana's sqlstring interpolation and millisecond timestamps, without changing the panel SQL."""

    def quoted(values):
        return ",".join("'" + value.replace("'", "''") + "'" for value in values)

    return (
        query.replace("${organization:sqlstring}", quoted(organizations))
        .replace("${workspace:sqlstring}", quoted(workspaces))
        .replace("${__from}", str(int(start.timestamp() * 1000)))
        .replace("${__to}", str(int(end.timestamp() * 1000)))
    )


def prometheus_test_files(directory):
    """Build promtool fixtures from the shipped dashboard queries and alert rules."""
    import yaml

    records = []
    for panel in dashboard("operations")["panels"]:
        for target in panel.get("targets", ()):
            records.append(
                {
                    "record": f"test:panel{panel['id']}_{target['refId']}",
                    "expr": target["expr"]
                    .replace("$job", "a13n-service")
                    .replace("$deployment", "local-beta")
                    .replace("$__rate_interval", "5m"),
                }
            )
    for source in sorted((MONITORING / "alerts").glob("*.yaml")):
        (directory / source.name).write_text(source.read_text())
        for group in yaml.safe_load(source.read_text())["groups"]:
            records.extend({"record": "test:alert_" + rule["alert"], "expr": rule["expr"]} for rule in group["rules"])
    (directory / "query-rules.yaml").write_text(
        yaml.safe_dump({"groups": [{"name": "query-validation", "rules": records}]})
    )
    (directory / "rules-test.yml").write_text((MONITORING / "tests" / "alerts.test.yaml").read_text())


# The real executable exporter, with one observation of each exported family used by the bundle.
# Run in a child process because OpenTelemetry installs its provider only once per process.
EXPORTER = """
import sys
from opentelemetry import metrics
# App definitions can import their instruments before the executable installs its provider.
app_meter = metrics.get_meter("acme.reports")
APP_PREVIEWS = app_meter.create_counter("acme.reports.previews")
from a13n_service.infra.telemetry import serve_metrics
serve_metrics("127.0.0.1", int(sys.argv[1]))
APP_DURATION = app_meter.create_histogram(
    "acme.reports.preview.duration", unit="s", explicit_bucket_boundaries_advisory=(0.1, 0.25, 1)
)
from a13n_service.infra.http import REQUEST_DURATION, RESPONSE_START, STREAM_ACTIVE, STREAM_DURATION
from a13n_service.infra.outbox import DELIVERIES, SETTLEMENT_FAILURES
from a13n_service.infra.sweeps import PASSES, ITEMS, DURATION, LAST_SUCCESS, DEADLINE
from a13n_service.runs.accept import RUNS_ACCEPTED
from a13n_service.runs.backlog import BACKLOG_OLDEST_AGE, BACKLOG_SIZE, BACKLOG_UPDATED, BACKLOG_LIMIT, OUTBOX_DEAD, OUTBOX_ALERT, OUTBOX_UPDATED
from a13n_service.runs.claim import QUEUE_WAIT, ATTEMPTS_STARTED
from a13n_service.runs.seal import ATTEMPT_DURATION, RUNS_SEALED, ATTEMPTS_ENDED, RUN_DURATION
from a13n_service.runs.worker import SLOTS, CLAIMS
from a13n_service.runs.inbox import WAIT
from a13n_service.runs.checkpoints import OBJECT_BYTES, CHECKPOINT_DURATION
from a13n_service.runs.environments.lifecycle import OPERATION_DURATION
from a13n_service.runs.environments.renewal import RENEWALS
from a13n_service.runs.schedules.lifecycle import DISPOSITIONS, DISPATCH_DELAY
from a13n_service.runs.schedules.dispatch import RETRIES
from a13n_service.runs.tool_executions.store import DURATION as EXECUTION_DURATION
from a13n_service.runs.stream import DROPPED, GAPS, SLOW_READERS
live = len(sys.argv) > 2 and sys.argv[2] == "live"
def observe():
    import math
    import time
    wave = 1 + 0.35 * math.sin(time.time() / 20) if live else 1
    tick = int(time.time()) if live else 0
    APP_PREVIEWS.add(2, {"result": "completed"})
    APP_DURATION.record(0.2, {"result": "completed"})
    labels = {"http.request.method": "GET", "http.route": "/api/v1/agents", "http.response.status_code": 200, "a13n.request.kind": "api"}
    for _ in range(3 + tick % 4):
        REQUEST_DURATION.record(0.025 * wave, labels)
    REQUEST_DURATION.record(0.002, dict(labels, **{"http.route": "/readyz", "a13n.request.kind": "probe"}))
    REQUEST_DURATION.record(0.003, dict(labels, **{"http.route": "/readyz", "http.response.status_code": 503, "a13n.request.kind": "probe"}))
    RESPONSE_START.record(0.012 * wave, dict(labels, **{"a13n.request.kind": "stream"}))
    STREAM_DURATION.record(10, {"result": "completed"})
    RUNS_ACCEPTED.add(3 + tick % 4, {"trigger": "input"})
    RUNS_SEALED.add(3 + tick % 4, {"status": "completed", "reason": "none"})
    RUNS_SEALED.add(1, {"status": "failed", "reason": "lease_expired"})
    QUEUE_WAIT.record(0.8 * wave)
    ATTEMPTS_STARTED.add(4 + tick % 4, {"reason": "initial"})
    ATTEMPTS_ENDED.add(3 + tick % 4, {"status": "succeeded", "reason": "completed"})
    ATTEMPTS_ENDED.add(1, {"status": "failed", "reason": "lease_expired"})
    ATTEMPT_DURATION.record(42, {"status": "failed"})
    RUN_DURATION.record(50, {"status": "failed"})
    SLOTS.set(3, {"state": "free"})
    SLOTS.set(1, {"state": "busy"})
    CLAIMS.record(0.01, {"result": "claimed"})
    WAIT.record(2 * wave)
    OBJECT_BYTES.record(1024, {"kind": "checkpoint"})
    CHECKPOINT_DURATION.record(0.1)
    for queue, size, age in (("runs", 3 + tick % 3, 5 * wave), ("inbox", 2, 9 * wave), ("schedule_due", 1, 2 * wave), ("schedule_dispatch", 1, 3 * wave), ("execution_lease_expired", 0, 0)):
        BACKLOG_SIZE.set(size, {"queue": queue})
        BACKLOG_OLDEST_AGE.set(age, {"queue": queue})
        BACKLOG_UPDATED.set(time.time(), {"queue": queue})
        BACKLOG_LIMIT.set(10000, {"queue": queue})
    OUTBOX_DEAD.set(0, {"kind": "email"})
    OUTBOX_ALERT.set(0, {"kind": "email"})
    OUTBOX_UPDATED.set(time.time())
    DELIVERIES.add(1, {"kind": "email", "result": "dead"})
    SETTLEMENT_FAILURES.add(1, {"kind": "email"})
    PASSES.add(1, {"sweep": "expire_leases", "result": "succeeded"})
    PASSES.add(1, {"sweep": "report_backlog", "result": "succeeded"})
    ITEMS.add(1, {"sweep": "expire_leases", "result": "failed"})
    DURATION.record(0.1, {"sweep": "expire_leases"})
    LAST_SUCCESS.set(time.time(), {"sweep": "expire_leases"})
    DEADLINE.set(60, {"sweep": "expire_leases"})
    OPERATION_DURATION.record(5, {"phase": "creating", "result": "completed", "certainty": "none"})
    RENEWALS.add(1, {"result": "renewed"})
    DISPOSITIONS.add(1, {"trigger": "scheduled", "status": "submitted", "reason": "none"})
    DISPATCH_DELAY.record(1, {"status": "submitted"})
    RETRIES.add(1)
    EXECUTION_DURATION.record(1, {"status": "succeeded"})
    DROPPED.add(1, {"reason": "write_buffer_full"})
    GAPS.add(1)
    SLOW_READERS.add(1)
    from pydantic_ai.models.instrumented import InstrumentationSettings
    settings = InstrumentationSettings(meter_provider=metrics.get_meter_provider(), version=5)
    for name in ("tenant-configured-model", "another-private-model"):
        attributes = {"gen_ai.provider.name": "openai", "gen_ai.operation.name": "chat", "gen_ai.token.type": "input", "gen_ai.request.model": name, "gen_ai.response.model": name}
        settings.tokens_histogram.record(300 * wave, attributes)
        settings.cost_histogram.record(0.1, attributes)
        settings.time_to_first_chunk_histogram.record(0.5 * wave, attributes)

STREAM_ACTIVE.add(8)
observe()
print("ready", flush=True)
# Keep the review fixture live: counters advance and shared snapshots remain fresh.
import select
if live:
    while not select.select([sys.stdin], [], [], 1)[0]:
        observe()
else:
    sys.stdin.read()
"""
