"""Opt-in Linux collector interoperability, using a disposable Prometheus only as a test receiver.

A13N_COLLECTOR_TEST=1 A13N_ENVD_TEST_BINARY=... uv run pytest <this file>
Images are pinned and must already be pulled. No production Service deployment includes a TSDB.
"""

import asyncio
import os
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from a13n_service.infra.db import transaction
from a13n_service.providers.collectors import Configuration, fragment_path
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.runs.environments.monitoring import pull

from .test_environments_envd import Daemons
from .test_monitoring_proxy import controlled, external_row

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def collector_prerequisites():
    assert sys.platform == "linux", "Collector interoperability requires Linux host networking"
    assert os.environ.get("A13N_COLLECTOR_TEST") == "1", "Set A13N_COLLECTOR_TEST=1 to run collector interoperability"
    assert os.environ.get("A13N_ENVD_TEST_BINARY"), "Set A13N_ENVD_TEST_BINARY to the built Envd executable"


IMAGES = {"vmagent": "victoriametrics/vmagent:v1.128.0", "alloy": "grafana/alloy:v1.10.2"}


def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def docker(*args):
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    return (stdout + stderr if args[0] == "logs" else stdout).decode().strip()


@asynccontextmanager
async def container(tmp_path, image, *args):
    name = "a13n-collector-test-" + uuid4().hex[:12]
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
        image,
        *args,
    )
    try:
        yield name
    finally:
        logs = await docker("logs", name)
        (tmp_path / (name + ".log")).write_text(logs)
        await docker("stop", "--time", "3", name)


async def eventually(check, *, seconds=25):
    async with asyncio.timeout(seconds):
        while True:
            try:
                result = await check()
                if result:
                    return result
            except httpx2.TransportError:
                pass
            await asyncio.sleep(0.1)


@pytest.mark.parametrize("kind", ["vmagent", "alloy"])
async def test_live_collector_scrapes_envd_reloads_and_preserves_operator_pipeline(service, tmp_path, kind):
    await controlled(service)
    receiver_port, collector_port = port(), port()
    receiver = f"http://127.0.0.1:{receiver_port}"
    endpoint = f"http://127.0.0.1:{collector_port}"
    receiver_config = tmp_path / "receiver.yml"
    receiver_config.write_text("scrape_configs: []\n")
    managed = tmp_path / "managed"
    managed.mkdir()
    config = Configuration(
        directory=managed,
        endpoint=endpoint,
        writer=True,
        receiver="prometheus.remote_write.operator.receiver" if kind == "alloy" else None,
    )
    fragment_path(config, kind).write_text("[]\n" if kind == "vmagent" else "\n")
    fragment_path(config, kind).chmod(0o600)
    if kind == "vmagent":
        operator = managed / "operator.yml"
        operator.write_text("scrape_config_files:\n  - /config/a13n-managed.yml\n")
        args = [
            "-httpListenAddr=" + f"127.0.0.1:{collector_port}",
            "-promscrape.config=/config/operator.yml",
            "-remoteWrite.url=" + receiver + "/api/v1/write",
            "-remoteWrite.tmpDataPath=/data/queue",
            "-remoteWrite.flushInterval=1s",
        ]
    else:
        operator = managed / "operator.alloy"
        operator.write_text(
            'prometheus.remote_write "operator" {\n  endpoint {\n    url = "'
            + receiver
            + '/api/v1/write"\n    queue_config { batch_send_deadline = "1s" }\n  }\n}\n'
        )
        args = [
            "run",
            "--server.http.listen-addr=" + f"127.0.0.1:{collector_port}",
            "--storage.path=/data/alloy",
            "/config",
        ]
    original = operator.read_text()
    daemons = Daemons(Path(os.environ["A13N_ENVD_TEST_BINARY"]), tmp_path)
    daemon = await daemons.start("device-test", monitoring=True, token="test-observation-token")
    try:
        async with container(
            tmp_path,
            "prom/prometheus:v3.2.1",
            "--config.file=/config/receiver.yml",
            "--storage.tsdb.path=/data/tsdb",
            "--web.listen-address=" + f"127.0.0.1:{receiver_port}",
            "--web.enable-remote-write-receiver",
        ):
            async with container(managed, IMAGES[kind], *args):
                async with httpx2.AsyncClient(timeout=2, trust_env=False) as client:
                    await eventually(lambda: ready(client, receiver + "/-/ready"))
                    await eventually(
                        lambda: ready(client, endpoint + "/-/ready" if kind == "alloy" else endpoint + "/health")
                    )
                    settings = service.runtime.settings.model_copy(
                        update={
                            "monitoring": service.runtime.settings.monitoring.model_copy(
                                update={"collectors": {kind: config}, "poll_seconds": 5}
                            )
                        }
                    )
                    runtime = replace(service.runtime, settings=settings)
                    result = await service.client.post(
                        f"{service.api}/metrics-data-sources",
                        json={"name": kind, "config": {"collection": "pull", "collector": kind}},
                    )
                    assert result.status_code == 201, result.text
                    source_id = result.json()["id"]
                    identity = await external_row(service, source_id, endpoint=daemon.endpoint)
                    await pull.reconcile(runtime, kind)
                    expression = 'envd_system_memory_total_bytes{environment_id="' + identity + '"}'

                    async def delivered():
                        response = await client.get(receiver + "/api/v1/query", params={"query": expression})
                        response.raise_for_status()
                        return response.json()["data"]["result"]

                    samples = await eventually(delivered)
                    assert samples[0]["metric"]["workspace_id"] == service.tenant.workspace_id
                    assert samples[0]["metric"]["organization_id"] == service.tenant.organization_id
                    assert float(samples[0]["value"][1]) > 0
                    assert operator.read_text() == original
                    assert fragment_path(config, kind).stat().st_mode & 0o777 == 0o600
                    assert not runtime.metrics_proxy.entries
                    if kind == "alloy":
                        before = await client.get(endpoint + "/metrics")
                        before.raise_for_status()
                        assert f'component_id="prometheus.scrape.a13n_service_{identity}"' in before.text
                    async with transaction(runtime.storage) as session:
                        row = await session.get_one(DataSourceRow, source_id)
                        row.enabled = False
                    await pull.reconcile(runtime, kind)
                    text = fragment_path(config, kind).read_text()
                    assert identity not in text and "test-observation-token" not in text
                    # The live collector must remove its old target, not merely accept a POST.
                    if kind == "vmagent":

                        async def removed():
                            response = await client.get(endpoint + "/api/v1/targets")
                            response.raise_for_status()
                            return not response.json()["data"]["activeTargets"]

                        await eventually(removed)
                    else:

                        async def removed():
                            response = await client.get(endpoint + "/metrics")
                            response.raise_for_status()
                            return f'component_id="prometheus.scrape.a13n_service_{identity}"' not in response.text

                        # Historical receiver samples and net_conntrack dialer counters
                        # outlive the scrape component. Its registered component metrics do not.
                        await eventually(removed)
                    assert operator.read_text() == original
    finally:
        await daemons.stop(daemon)


async def ready(client, url):
    return (await client.get(url)).status_code == 200
