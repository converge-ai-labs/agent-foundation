"""Opt-in pinned Prometheus/Grafana interoperability against real Service facts.

A13N_MONITORING_TEST=1 uv run pytest packages/a13n-service/tests/test_monitoring_bundle_live.py
Images must already be pulled. Optional A13N_MONITORING_REVIEW_FILE holds the owned fixture for UI review
until the corresponding .done file exists (30 minute deadline). All containers are removed on exit.
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
import yaml
from a13n_service.infra.db import transaction
from sqlalchemy import text
from sqlalchemy.engine import make_url

from .monitoring_support import EXPORTER, MONITORING, dashboard, expand_usage, usage_queries
from .test_monitoring_collectors_live import docker, eventually, port, ready
from .test_usage_analytics import observed_run

pytestmark = pytest.mark.anyio
PROMETHEUS = "prom/prometheus:v3.2.1"
GRAFANA = "grafana/grafana:12.3.1"


@pytest.fixture(autouse=True)
def prerequisites():
    assert sys.platform == "linux", "Monitoring interoperability requires Linux host networking"
    assert os.environ.get("A13N_MONITORING_TEST") == "1", (
        "Set A13N_MONITORING_TEST=1 to run monitoring interoperability"
    )


@asynccontextmanager
async def owned_container(tmp_path, image, *args, env=()):
    name = "a13n-monitoring-test-" + uuid4().hex[:12]
    environment = [argument for value in env for argument in ("-e", value)]
    await docker(
        "run",
        "--pull=never",
        "--rm",
        "-d",
        "--name",
        name,
        "--network=host",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={tmp_path},dst=/config,readonly",
        "--tmpfs",
        "/data:mode=1777",
        *environment,
        image,
        *args,
    )
    try:
        yield name
    finally:
        (tmp_path / (name + ".log")).write_text(await docker("logs", name))
        await docker("stop", "--time", "3", name)


def expressions():
    return {
        f"{panel['title']} / {target['refId']}": target["expr"]
        for panel in dashboard("operations")["panels"]
        for target in panel.get("targets", ())
    }


def expand_promql(expr):
    return expr.replace("$job", "a13n-service").replace("$deployment", "local-beta").replace("$__rate_interval", "1m")


@pytest.mark.timeout(1900)
async def test_prometheus_rules_queries_and_grafana_postgres_import(service, scripted_model, runs_kit, tmp_path):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    started = datetime.now(UTC) - timedelta(minutes=2)
    from decimal import Decimal

    await observed_run(
        service,
        runs_kit,
        agent,
        started=started,
        records=[
            ("scripted", 100, 20, 80, Decimal("0.25"), started),
            ("scripted", 50, 10, 0, None, started),
        ],
    )
    # Fictional multi-day business activity for rendered reviews; all rows use the real schema.
    reporting = await runs_kit.add_agent(service, "Reporting assistant", "scripted")
    for day in range(1, 7):
        at = started - timedelta(days=day)
        await observed_run(
            service,
            runs_kit,
            agent if day % 2 else reporting,
            started=at,
            seconds=8 + day * 4,
            outcome="failed" if day == 5 else "completed",
            records=[
                ("scripted", 1800 + day * 300, 240 + day * 50, 900, Decimal("0.12") * day, at),
                ("scripted", 1200, 160, 600, None if day == 3 else Decimal("0.05"), at),
            ],
        )
    exporter_port, prom_port, grafana_port = port(), port(), port()
    prom, grafana = f"http://127.0.0.1:{prom_port}", f"http://127.0.0.1:{grafana_port}"
    for source in sorted((MONITORING / "alerts").glob("*.yaml")):
        (tmp_path / source.name).write_text(source.read_text())
    (tmp_path / "prometheus.yml").write_text(
        yaml.safe_dump(
            {
                "global": {"scrape_interval": "1s", "evaluation_interval": "1s"},
                "rule_files": ["/config/availability.yaml", "/config/capacity.yaml", "/config/execution.yaml"],
                "scrape_configs": [
                    {
                        "job_name": "a13n-service",
                        "static_configs": [
                            {
                                "targets": [f"127.0.0.1:{exporter_port}"],
                                "labels": {"deployment": "local-beta", "role": "all"},
                            }
                        ],
                    }
                ],
            }
        )
    )
    # Unit-evaluate stale replica exclusion BEFORE max, deployment isolation and optional alert boundaries.
    unit = {
        "rule_files": ["/config/capacity.yaml", "/config/availability.yaml"],
        "evaluation_interval": "1m",
        "tests": [
            {
                "interval": "1m",
                "input_series": [
                    {
                        "series": 'a13n_backlog_oldest_age_seconds{job="a13n-service",deployment="a",instance="stale",queue="runs"}',
                        "values": "900+0x20",
                    },
                    {
                        "series": 'a13n_backlog_updated_seconds{job="a13n-service",deployment="a",instance="stale",queue="runs"}',
                        "values": "0+0x20",
                    },
                    {
                        "series": 'a13n_backlog_oldest_age_seconds{job="a13n-service",deployment="a",instance="fresh",queue="runs"}',
                        "values": "5+0x20",
                    },
                    {
                        "series": 'a13n_backlog_updated_seconds{job="a13n-service",deployment="a",instance="fresh",queue="runs"}',
                        "values": "0+60x20",
                    },
                    {
                        "series": 'a13n_backlog_oldest_age_seconds{job="a13n-service",deployment="b",instance="fresh",queue="runs"}',
                        "values": "700+0x20",
                    },
                    {
                        "series": 'a13n_backlog_updated_seconds{job="a13n-service",deployment="b",instance="fresh",queue="runs"}',
                        "values": "0+60x20",
                    },
                ],
                "promql_expr_test": [
                    {
                        "expr": 'max by (deployment,queue) (a13n_backlog_oldest_age_seconds{job="a13n-service"} and on (job,deployment,instance,queue) (time() - a13n_backlog_updated_seconds{job="a13n-service"} < 60))',
                        "eval_time": "20m",
                        "exp_samples": [
                            {"labels": '{deployment="a",queue="runs"}', "value": 5},
                            {"labels": '{deployment="b",queue="runs"}', "value": 700},
                        ],
                    }
                ],
                "alert_rule_test": [
                    {
                        "eval_time": "20m",
                        "alertname": "A13nBacklogStuck",
                        "exp_alerts": [
                            {
                                "exp_labels": {
                                    "job": "a13n-service",
                                    "deployment": "b",
                                    "queue": "runs",
                                    "severity": "warning",
                                },
                                "exp_annotations": {
                                    "summary": "Due work is not progressing",
                                    "description": "b queue runs: oldest due item exceeded 10m. Check queue-specific owners; sleeping/retry-backoff work is excluded.",
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    (tmp_path / "rules-test.yml").write_text(yaml.safe_dump(unit))
    for args in [
        ("check", "rules", "/config/availability.yaml", "/config/capacity.yaml", "/config/execution.yaml"),
        ("test", "rules", "/config/rules-test.yml"),
    ]:
        await docker(
            "run",
            "--pull=never",
            "--rm",
            "--network=none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--entrypoint",
            "/bin/promtool",
            "--mount",
            f"type=bind,src={tmp_path},dst=/config,readonly",
            PROMETHEUS,
            *args,
        )

    exporter = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        EXPORTER,
        str(exporter_port),
        "live",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(20):
            assert await exporter.stdout.readline() == b"ready\n"
        async with owned_container(
            tmp_path,
            PROMETHEUS,
            "--config.file=/config/prometheus.yml",
            "--storage.tsdb.path=/data/tsdb",
            f"--web.listen-address=127.0.0.1:{prom_port}",
        ):
            async with owned_container(
                tmp_path,
                GRAFANA,
                env=(
                    "GF_PATHS_DATA=/data",
                    "GF_PATHS_LOGS=/data/logs",
                    "GF_PATHS_PLUGINS=/data/plugins",
                    "GF_PATHS_PROVISIONING=/data/provisioning",
                    "GF_SERVER_HTTP_ADDR=127.0.0.1",
                    f"GF_SERVER_HTTP_PORT={grafana_port}",
                    "GF_AUTH_ANONYMOUS_ENABLED=true",
                    "GF_AUTH_ANONYMOUS_ORG_ROLE=Admin",
                    "GF_SECURITY_ADMIN_PASSWORD=monitoring-test-only",
                    "GF_ANALYTICS_REPORTING_ENABLED=false",
                    "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
                    "GF_USERS_DEFAULT_THEME=light",
                ),
            ):
                async with httpx2.AsyncClient(
                    timeout=20, trust_env=False, auth=("admin", "monitoring-test-only")
                ) as client:
                    await eventually(lambda: ready(client, prom + "/-/ready"))
                    await eventually(lambda: ready(client, grafana + "/api/health"), seconds=60)

                    async def scraped():
                        result = await client.get(
                            prom + "/api/v1/query", params={"query": 'up{job="a13n-service"} == 1'}
                        )
                        return result.json().get("data", {}).get("result")

                    await eventually(scraped)
                    for title, expr in expressions().items():
                        response = await client.get(prom + "/api/v1/query", params={"query": expand_promql(expr)})
                        assert response.status_code == 200, (title, response.text)
                        assert response.json()["status"] == "success", (title, response.text)
                    # A real read-only database login proves the documented grant inventory is complete.
                    url = make_url(service.runtime.settings.database.url.get_secret_value())
                    async with transaction(service.runtime.storage) as session:
                        await session.execute(
                            text("CREATE ROLE a13n_usage_reader LOGIN PASSWORD 'monitoring-test-only'")
                        )
                        await session.execute(text(f'GRANT CONNECT ON DATABASE "{url.database}" TO a13n_usage_reader'))
                        await session.execute(text("GRANT USAGE ON SCHEMA public TO a13n_usage_reader"))
                        await session.execute(
                            text(
                                "GRANT SELECT ON organizations, workspaces, agents, models, runs, usage_records TO a13n_usage_reader"
                            )
                        )
                    sources = [
                        {
                            "name": "Beta Prometheus",
                            "uid": "beta-prom",
                            "type": "prometheus",
                            "url": prom,
                            "access": "proxy",
                            "jsonData": {"httpMethod": "POST", "timeInterval": "1s"},
                        },
                        {
                            "name": "Beta PostgreSQL",
                            "uid": "beta-pg",
                            "type": "grafana-postgresql-datasource",
                            "access": "proxy",
                            "url": f"{url.host}:{url.port}",
                            "user": "a13n_usage_reader",
                            "secureJsonData": {"password": "monitoring-test-only"},
                            "jsonData": {
                                "database": url.database,
                                "sslmode": "disable",
                                "postgresVersion": 1700,
                                "timescaledb": False,
                            },
                        },
                    ]
                    for source in sources:
                        result = await client.post(grafana + "/api/datasources", json=source)
                        assert result.status_code == 200, (source["name"], result.text)
                        health = await client.get(grafana + f"/api/datasources/uid/{source['uid']}/health")
                        assert health.status_code == 200 and health.json()["status"] == "OK", health.text
                    for kind, uid in (("operations", "beta-prom"), ("usage", "beta-pg")):
                        model = dashboard(kind)
                        model["templating"]["list"][0]["current"] = {"text": uid, "value": uid}
                        if kind == "usage":
                            for variable, value in zip(
                                model["templating"]["list"][1:],
                                (service.tenant.organization_id, service.tenant.workspace_id),
                                strict=True,
                            ):
                                variable["current"] = {"text": value, "value": value}
                        model["time"] = (
                            {"from": "now-5m", "to": "now"}
                            if kind == "operations"
                            else {
                                "from": (started - timedelta(days=7)).isoformat(),
                                "to": (started + timedelta(minutes=5)).isoformat(),
                            }
                        )
                        imported = await client.post(
                            grafana + "/api/dashboards/db", json={"dashboard": model, "overwrite": True}
                        )
                        assert imported.status_code == 200, imported.text
                        loaded = await client.get(grafana + f"/api/dashboards/uid/{model['uid']}")
                        assert loaded.json()["dashboard"]["editable"] is True
                    for title, query in usage_queries().items():
                        sql = expand_usage(
                            query,
                            organizations=[service.tenant.organization_id],
                            workspaces=[service.tenant.workspace_id],
                            start=started - timedelta(minutes=1),
                            end=started + timedelta(minutes=5),
                        )
                        response = await client.post(
                            grafana + "/api/ds/query",
                            json={
                                "from": str(int((started - timedelta(minutes=1)).timestamp() * 1000)),
                                "to": str(int((started + timedelta(minutes=5)).timestamp() * 1000)),
                                "queries": [
                                    {
                                        "refId": "A",
                                        "datasource": {"type": "grafana-postgresql-datasource", "uid": "beta-pg"},
                                        "rawSql": sql,
                                        "format": "table",
                                    }
                                ],
                            },
                        )
                        assert response.status_code == 200, (title, response.text)
                        assert not response.json()["results"]["A"].get("error"), (title, response.text)
                    if review := os.environ.get("A13N_MONITORING_REVIEW_FILE"):
                        location = Path(review)
                        location.write_text(
                            json.dumps(
                                {
                                    "grafana": grafana,
                                    "prometheus": prom,
                                    "organization": service.tenant.organization_id,
                                    "workspace": service.tenant.workspace_id,
                                    "started": started.isoformat(),
                                },
                                indent=2,
                            )
                        )
                        async with asyncio.timeout(1800):
                            while not location.with_suffix(".done").exists():
                                await asyncio.sleep(1)
    finally:
        if exporter.stdin:
            exporter.stdin.close()
        await asyncio.wait_for(exporter.wait(), timeout=10)
