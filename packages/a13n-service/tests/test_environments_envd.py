"""External envd targets against the real `a13n-envd` that `A13N_ENVD_TEST_BINARY` names: registration by endpoint
and token, runs acting in the target, the device identity every connection expects, and endpoint and token changes.

`make eip-integration-test`, which CI runs, builds the daemon and sets the variable; the Service suite leaves this
module out, and without the variable its tests are skipped.
"""

import asyncio
import json
import logging
import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_service.runs.environments.adapters import execution_connector

from .environments_support import EXTERNAL_TOKEN as TOKEN
from .environments_support import change, details, register, revealed, stored, target

BINARY = os.environ.get("A13N_ENVD_TEST_BINARY")
# Skip before any fixture builds the service.
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        BINARY is None, reason="set A13N_ENVD_TEST_BINARY to an a13n-envd build to run external targets"
    ),
]


@dataclass
class Daemon:
    device_id: str
    port: int
    workspace: Path
    process: asyncio.subprocess.Process

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class Daemons:
    """Real daemons serving HTTP on loopback with full control of their workspace; all stop with the test."""

    def __init__(self, binary: Path, directory: Path) -> None:
        self.binary, self.directory, self.running = binary, directory, list[Daemon]()

    async def start(
        self,
        device_id: str,
        *,
        token: str = TOKEN,
        port: int | None = None,
        monitoring: bool = False,
        daemon_config: str | None = None,
    ) -> Daemon:
        if port is None:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        root = self.directory / f"{device_id}-{port}"
        workspace, runtime = root / "workspace", root / "runtime"
        workspace.mkdir(parents=True)
        runtime.mkdir()
        (root / "token").write_text(token)
        config = root / "envd.json"
        values = {"default_working_directory": str(workspace), "full_control": True}
        if monitoring:
            values["monitoring"] = {"sample_interval_ms": 1000}
        config.write_text(json.dumps(values))
        process = await asyncio.create_subprocess_exec(
            str(self.binary),
            "--config",
            str(config),
            env={
                "PATH": os.environ["PATH"],
                **({"A13N_ENVD_CONFIG_JSON": daemon_config} if daemon_config is not None else {}),
                "A13N_ENVD_DEVICE_ID": device_id,
                "A13N_ENVD_RUNTIME_DIR": str(runtime),
                "A13N_ENVD_TRANSPORT": "http",
                "A13N_ENVD_HTTP_BIND": f"127.0.0.1:{port}",
                "A13N_ENVD_HTTP_CREDENTIAL_FILE": str(root / "token"),
                "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "loopback",
            },
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        daemon = Daemon(device_id, port, workspace.resolve(), process)
        self.running.append(daemon)
        async with asyncio.timeout(10):
            while True:
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    await asyncio.sleep(0.02)
                    continue
                writer.close()
                await writer.wait_closed()
                return daemon

    async def stop(self, daemon: Daemon) -> None:
        if daemon.process.returncode is None:
            daemon.process.terminate()
            await daemon.process.wait()


@pytest.fixture
async def daemons(tmp_path: Path) -> AsyncIterator[Daemons]:
    assert BINARY is not None
    started = Daemons(Path(BINARY), tmp_path / "daemons")
    try:
        yield started
    finally:
        for daemon in started.running:
            await started.stop(daemon)


async def test_managed_sampler_bootstrap_enables_real_envd_without_exporter(daemons):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime
    from a13n_harness.providers.environment.remote_envd.configuration import (
        HttpEnvdConnectionConfiguration,
        HttpEnvdCredential,
    )
    from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
    from a13n_service.providers.environment.metrics import MetricsLaunch
    from a13n_service.providers.environments.e2b.configuration import E2BRecipe
    from a13n_service.providers.environments.e2b.management import E2BManagement
    from pydantic import SecretStr

    management = E2BManagement(
        E2BRecipe(execution="envd_http", template="team-envd"),
        environment_id="env_sampled",
        state=None,
        runtime=E2BProviderRuntime(SecretStr("test-key")),
        operation_id="create",
    )
    management.configure_metrics(MetricsLaunch({}))
    run = AsyncMock()
    await management._bootstrap_envd(SimpleNamespace(commands=SimpleNamespace(run=run)))
    bootstrap = run.call_args_list[0].kwargs["envs"]
    assert not any(name.startswith("OTEL_") for name in bootstrap)
    daemon = await daemons.start("env_sampled", daemon_config=bootstrap["A13N_ENVD_CONFIG_JSON"])
    async with HttpEnvdProviderRuntime(
        HttpEnvdConnectionConfiguration(endpoint=daemon.endpoint), HttpEnvdCredential(token=SecretStr(TOKEN))
    ) as runtime:
        device = await runtime.acquire_device(expected_device_id="env_sampled")
        assert "device.metrics" in device.descriptor.available_methods
        async with asyncio.timeout(15):
            while True:
                report = await device.metrics()
                if report.sample is not None:
                    break
                await asyncio.sleep(0.05)
        assert report.sample.system.memory_total_bytes > 0
        assert report.sample_interval_ms == 10_000


async def test_proxy_reads_real_envd_without_session_and_exports_selected_source(service, daemons, monkeypatch):
    import time

    from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
    from a13n_service.infra.db import transaction
    from a13n_service.runs.environments.monitoring import collection
    from a13n_service.runs.environments.monitoring.query import EnvironmentQuery
    from a13n_service.runs.environments.tables import EnvironmentRow

    from .test_monitoring_export import receiver
    from .test_monitoring_proxy import controlled, external_row

    proxy = await controlled(service)
    daemon = await daemons.start("device-test", monitoring=True)

    def no_session(*args, **kwargs):
        pytest.fail("Proxy collection must not open an execution Session")

    monkeypatch.setattr(HttpEnvdProviderRuntime, "open_session", no_session)
    async with receiver() as (endpoint, deliveries):
        created = await service.client.post(
            f"{service.api}/metrics-data-sources",
            json={
                "name": "Real proxy",
                "config": {"collection": "proxy", "export": {"endpoint": endpoint}},
                "export_credentials": {"headers": {"authorization": "Bearer proxy-wire"}},
            },
        )
        assert created.status_code == 201, created.text
        environment = await external_row(service, created.json()["id"], endpoint=daemon.endpoint)
        from a13n_service.runs.environments import external

        async with transaction(service.runtime.storage) as session:
            row = await session.get(EnvironmentRow, environment)
            row.token = external.seal(service.runtime.keys, row, TOKEN)
        original = collection.read

        async def checked_read(runtime, selection, *, credential_cache=None):
            assert runtime.storage.engine.pool.checkedout() == 0
            return await original(runtime, selection, credential_cache=credential_cache)

        monkeypatch.setattr(collection, "read", checked_read)
        async with asyncio.timeout(10):
            while True:
                await proxy.poll(environment)
                entry = proxy.entries[environment]
                if entry.cache.report is not None and entry.cache.report.sample is not None:
                    break
                await asyncio.sleep(0.05)
        assert entry.cache.system_info is not None
        assert entry.cache.status == "ready"
        task = asyncio.create_task(proxy.exporter.run())
        try:
            await asyncio.wait_for(proxy.exporter.queue.join(), timeout=5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await proxy.exporter.close()
        assert len(deliveries) == 1
        assert deliveries[0][1]["authorization"] == "bearer proxy-wire"
        metrics = deliveries[0][2].resource_metrics[0].scope_metrics[0].metrics
        memory = next(metric for metric in metrics if metric.name == "envd_system_memory_total_bytes")
        point = memory.gauge.data_points[0]
        assert point.as_int > 0
        assert {a.key: a.value.string_value for a in point.attributes}["environment_id"] == environment
        now = time.time()
        body = EnvironmentQuery(metric="system_memory_used_bytes", start=now - 3600, end=now + 1, step=60)
        response = await service.client.post(
            f"{service.api}/environments/{environment}/metrics/query", json=body.model_dump()
        )
        assert response.status_code == 200, response.text
        assert response.json()["source"] == "temporary"
        assert response.json()["history"]["series"][0]["points"][0][2] > 0


async def test_service_native_settings_export_real_envd_samples(tmp_path: Path) -> None:
    from a13n_service.resources.metrics_data_sources.schemas import Export
    from a13n_service.runs.environments.monitoring.launch import exporter_environment
    from a13n_service.runs.environments.tables import EnvironmentRow
    from fastapi import FastAPI, Request, Response
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    from .conftest import listening

    received = asyncio.Queue()
    app = FastAPI()

    @app.post("/native-metrics")
    async def ingest(request: Request):
        await received.put(
            (request.headers.get("authorization"), ExportMetricsServiceRequest.FromString(await request.body()))
        )
        return Response(content=b"", media_type="application/x-protobuf")

    async with listening(app) as endpoint:
        environment = EnvironmentRow(id="env_native", workspace_id="ws_native", organization_id="org_native")
        values = exporter_environment(
            Export(endpoint=endpoint + "/native-metrics", interval_seconds=5),
            {"authorization": "Bearer write,=% token"},
            environment,
        )
        process = await asyncio.create_subprocess_exec(
            BINARY,
            env={
                **{name: os.environ[name] for name in ("PATH", "SystemRoot") if name in os.environ},
                **values,
                "A13N_ENVD_RUNTIME_DIR": str(tmp_path / "runtime"),
                "A13N_ENVD_CONFIG_JSON": json.dumps(
                    {"device_id": environment.id, "default_working_directory": str(tmp_path)}
                ),
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            authorization, request = await asyncio.wait_for(received.get(), timeout=20)
            assert authorization == "Bearer write,=% token"
            resource = request.resource_metrics[0]
            attributes = {item.key: item.value.string_value for item in resource.resource.attributes}
            assert attributes["environment_id"] == "env_native"
            assert attributes["workspace_id"] == "ws_native"
            assert attributes["service.instance.id"] == "env_native"
            metric = next(
                metric
                for scope in resource.scope_metrics
                for metric in scope.metrics
                if metric.name == "envd_system_memory_total_bytes"
            )
            point = metric.gauge.data_points[0]
            assert {item.key: item.value.string_value for item in point.attributes}["device_id"] == "env_native"
            assert point.as_double > 0
        finally:
            process.stdin.close()
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise
        assert process.returncode == 0, stderr.decode()
        assert b"write,=% token" not in stderr


async def test_a_registered_target_serves_runs_and_keeps_its_token_sealed(  # type: ignore[no-untyped-def]
    service, daemons, scripted_model, runs_kit, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    laptop = await daemons.start("device-laptop")
    registered = await register(service, laptop.endpoint + "/")
    assert registered.status_code == 201, registered.text
    view = registered.json()
    fields = ("provider_id", "template_id", "device_id", "endpoint", "name", "status", "owner_principal_id")
    assert [view[name] for name in fields] == [
        None,
        None,
        "device-laptop",
        laptop.endpoint,
        "device-laptop",
        "ready",
        service.tenant.principal_id,
    ]
    listed = await service.client.get(f"{service.api}/environments")
    assert "token" not in view and TOKEN not in registered.text + listed.text
    row = await stored(service, view["id"])
    assert row.token is not None and TOKEN not in json.dumps(row.token) and revealed(service, row) == TOKEN.encode()

    # A run mounting the target acts in the daemon's workspace.
    agent = await runs_kit.create_agent(service, scripted_model)
    command = "echo external-$((6 * 7)) > proof.txt && cat proof.txt"
    scripted_model.call("shell_exec", {"command": command}, call_id="call_write")
    scripted_model.say("Written.")
    mount = {"name": "workspace", "environment_id": view["id"], "working_directory": str(laptop.workspace)}
    submitted = await runs_kit.start_thread(service, agent, "Write the proof", environments=[mount])
    await (await runs_kit.attempt(service))
    run = await runs_kit.sealed(service, submitted["run"]["id"])
    assert run["status"] == "completed", run["failure"]
    await scripted_model.request()
    answered = await scripted_model.request()
    [result] = [message for message in answered["messages"] if message["role"] == "tool"]
    assert "external-42" in result["content"]
    assert (laptop.workspace / "proof.txt").read_text() == "external-42\n"
    assert TOKEN not in caplog.text


async def test_every_connection_expects_the_registered_device(service, daemons) -> None:  # type: ignore[no-untyped-def]
    laptop = await daemons.start("device-laptop")
    environment_id = (await register(service, laptop.endpoint)).json()["id"]

    # The endpoint now reaches another daemon: the next connection is refused, and so is re-verifying it.
    await daemons.stop(laptop)
    await daemons.start("device-impostor", port=laptop.port)
    adapter = await execution_connector(service.runtime, await target(service, environment_id))
    with pytest.raises(EnvironmentProviderError) as refused:
        await adapter.open()
    # The daemon refuses a connection that expects another device before any session opens.
    assert refused.value.code == "provider_device_mismatch"
    mismatch = await change(service, environment_id, {"token": TOKEN})
    assert mismatch.status_code == 409 and details(mismatch)["reason"] == "provider_device_mismatch", mismatch.text

    # The device moved and its token rotated: a new endpoint comes with its token, stored once they reach it.
    moved = await daemons.start("device-laptop", token="rotated-token")
    alone = await change(service, environment_id, {"endpoint": moved.endpoint})
    assert alone.status_code == 400 and details(alone)["field"] == "token"
    # A daemon refusing the token answers no differently from one that cannot be reached.
    refused_token = await change(service, environment_id, {"endpoint": moved.endpoint, "token": TOKEN})
    assert refused_token.status_code == 503, refused_token.text
    assert details(refused_token)["reason"] == "provider_connection_failed"
    changed = await change(
        service, environment_id, {"endpoint": moved.endpoint, "token": "rotated-token", "name": "Mine"}
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["endpoint"], changed.json()["name"]) == (moved.endpoint, "Mine")
    row = await stored(service, environment_id)
    assert (row.endpoint, row.provider_identity, row.handle) == (moved.endpoint, None, None)
    assert revealed(service, row) == b"rotated-token" and "rotated-token" not in changed.text
    adapter = await execution_connector(service.runtime, await target(service, environment_id))
    async with await adapter.open():
        pass


async def test_reverse_device_real_envd_export_cross_replica_query_and_revocation(service, tmp_path, monkeypatch):
    import time
    from dataclasses import replace

    from a13n_envd_client import EIPDeviceConnection
    from a13n_service.runs.environments.monitoring.proxy import Proxy, lease_key
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    from .conftest import listening
    from .test_monitoring_devices import allow_loopback, registration
    from .test_monitoring_export import receiver
    from .test_monitoring_proxy import controlled

    owner = await controlled(service)
    allow_loopback(service, monkeypatch)
    owner_runtime = service.app.state.runtime
    peer = Proxy(replace(owner_runtime, metrics_proxy=None))
    tasks = [asyncio.create_task(owner.route()), asyncio.create_task(peer.route())]
    await asyncio.wait_for(asyncio.gather(owner.ready.wait(), peer.ready.wait()), timeout=5)

    async def no_session(*args, **kwargs):
        pytest.fail("Reverse monitoring must not create an execution Session")

    monkeypatch.setattr(EIPDeviceConnection, "open_session", no_session)
    original_metrics = EIPDeviceConnection.metrics

    async def metrics(device):
        assert service.runtime.storage.engine.pool.checkedout() == 0
        return await original_metrics(device)

    monkeypatch.setattr(EIPDeviceConnection, "metrics", metrics)
    try:
        async with receiver() as (destination, deliveries), listening(service.app) as endpoint:
            created = await service.client.post(
                f"{service.api}/metrics-data-sources",
                json={
                    "name": "Reverse export",
                    "config": {"collection": "proxy", "export": {"endpoint": destination}},
                    "export_credentials": {"headers": {"authorization": "Bearer reverse-write"}},
                },
            )
            assert created.status_code == 201, created.text
            registered = await registration(service, source=created.json()["id"])
            row, token = registered.json()["device"], registered.json()["token"]
            url = endpoint.replace("http://", "ws://") + f"/api/v1/devices/{row['id']}/connect"
            credential = tmp_path / "reverse-token"
            credential.write_text(token)
            credential.chmod(0o600)
            process = await asyncio.create_subprocess_exec(
                BINARY,
                env={
                    **{name: os.environ[name] for name in ("PATH", "SystemRoot") if name in os.environ},
                    "A13N_ENVD_DEVICE_ID": row["device_id"],
                    "A13N_ENVD_RUNTIME_DIR": str(tmp_path / "reverse-runtime"),
                    "A13N_ENVD_TRANSPORT": "reverse_websocket",
                    "A13N_ENVD_REVERSE_WS_URL": url,
                    "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential),
                    "A13N_ENVD_CONFIG_JSON": json.dumps(
                        {"default_working_directory": str(tmp_path), "monitoring": {"sample_interval_ms": 1000}}
                    ),
                },
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(10):
                    while row["id"] not in owner.reverse:
                        assert process.returncode is None
                        await asyncio.sleep(0.01)
                    await owner.reverse[row["id"]].acquire_device(expected_device_id=row["device_id"])
                    while True:
                        await owner.poll(row["id"])
                        entry = owner.entries[row["id"]]
                        if entry.cache.report is not None and entry.cache.report.sample is not None:
                            break
                        await asyncio.sleep(0.02)
                # A second replica routes reads to the carrier owner; it never claims or dials the Device.
                service.app.state.runtime = replace(owner_runtime, metrics_proxy=peer)
                now = time.time()
                query = await service.client.post(
                    f"{service.api}/devices/{row['id']}/metrics/query",
                    json={
                        "metric": "system_memory_used_bytes",
                        "start": now - 3600,
                        "end": now + 1,
                        "step": 60,
                    },
                )
                assert query.status_code == 200, query.text
                assert query.json()["history"]["series"][0]["points"][0][2] > 0
                assert not peer.entries
                with pytest.raises(InvalidStatus):
                    async with connect(
                        url,
                        subprotocols=["eip.v1"],
                        additional_headers={"authorization": "Bearer " + token},
                        compression=None,
                    ):
                        pytest.fail("Second replica admitted an already owned Device")
                service.app.state.runtime = owner_runtime
                exporter = asyncio.create_task(owner.exporter.run())
                try:
                    await asyncio.wait_for(owner.exporter.queue.join(), timeout=5)
                finally:
                    exporter.cancel()
                    await asyncio.gather(exporter, return_exceptions=True)
                assert len(deliveries) == 1
                scheme, credential_value = deliveries[0][1]["authorization"].split(" ", 1)
                assert scheme.lower() == "bearer" and credential_value == "reverse-write"
                metric = next(
                    metric
                    for metric in deliveries[0][2].resource_metrics[0].scope_metrics[0].metrics
                    if metric.name == "envd_system_memory_total_bytes"
                )
                attributes = {a.key: a.value.string_value for a in metric.gauge.data_points[0].attributes}
                assert attributes["device_registration_id"] == row["id"]
                assert "environment_id" not in attributes
                rotated = await service.client.post(
                    f"{service.api}/devices/{row['id']}/rotate-credential",
                    headers={"if-match": registered.headers["etag"]},
                )
                assert rotated.status_code == 200, rotated.text
                await owner.poll(row["id"])
                assert row["id"] not in owner.reverse
                assert await service.runtime.redis.get(lease_key(row["id"])) is None
                with pytest.raises(InvalidStatus):
                    async with connect(
                        url,
                        subprotocols=["eip.v1"],
                        additional_headers={"authorization": "Bearer " + token},
                        compression=None,
                    ):
                        pytest.fail("Rotated credential reconnected")
            finally:
                if process.returncode is None:
                    process.terminate()
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
                assert token.encode() not in stderr
    finally:
        service.app.state.runtime = owner_runtime
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for identity, entry in list(owner.entries.items()):
            await owner.drop(identity, expected=entry)
        await owner.exporter.close()
        await peer.exporter.close()


@pytest.mark.parametrize("lost_cleanup", [False, True])
@pytest.mark.parametrize("successor", [False, True])
async def test_checkpointed_sessions_are_recovered_only_within_the_same_run(
    service, daemons, scripted_model, runs_kit, monkeypatch, lost_cleanup, successor
):
    from a13n_envd_client import EIPSession
    from a13n_service.infra.db import transaction
    from a13n_service.runs import checkpoints
    from a13n_service.runs.seal import LeaseExpirer
    from a13n_service.runs.tables import AttemptRow, RunRow
    from sqlalchemy import update

    await runs_kit.pause_sweeps(service)
    laptop = await daemons.start("device-recovery")
    environment_id = (await register(service, laptop.endpoint)).json()["id"]
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.call("shell_exec", {"command": "echo first >> proof.txt"}, call_id="call_first")
    first_gate = asyncio.Event()
    scripted_model.say("Interrupted", gate=first_gate)
    submitted = await runs_kit.start_thread(
        service,
        agent,
        "Write proof",
        environments=[
            {"name": "workspace", "environment_id": environment_id, "working_directory": str(laptop.workspace)}
        ],
    )
    run_id = submitted["run"]["id"]

    async def saved_state():
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(RunRow, run_id)
            pointer = checkpoints.StatePointer.model_validate(row.checkpoint)
        result = await checkpoints.load_state(service.runtime.objects, pointer)
        assert result is not None
        return result

    first = await runs_kit.attempt(service)
    await scripted_model.request()
    await scripted_model.request()
    saved = await saved_state()
    assert saved.format == checkpoints.FORMAT
    assert set(saved.envd_sessions) == {"workspace"}
    original = saved.envd_sessions["workspace"].descriptor
    assert TOKEN not in saved.model_dump_json()

    async def lose_remote_cleanup(session):
        # Simulate the remote effect of worker loss while still releasing local
        # test tasks/carriers. Production cleanup remains unchanged.
        session._finish()

    with monkeypatch.context() as patch:
        if lost_cleanup:
            patch.setattr(EIPSession, "close", lose_remote_cleanup)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    first_gate.set()
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.run_id == run_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(service.runtime, batch=10)()
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))

    if successor:
        # Interrupt keeps the last checkpoint, including hints from the previous
        # attempt. A new user turn inherits history but must not attach its Session.
        response = await service.client.post(f"{service.api}/runs/{run_id}/interrupt")
        assert response.status_code == 200, response.text
        assert (await saved_state()).envd_sessions
        response = await runs_kit.submit(service, submitted["thread"]["id"], runs_kit.message(agent, "Continue"))
        assert response.status_code == 201, response.text
        run_id = response.json()["run"]["id"]

    scripted_model.call("shell_exec", {"command": "echo second >> proof.txt"}, call_id="call_second")
    gate = asyncio.Event()
    scripted_model.say("Recovered", gate=gate)
    second = await runs_kit.attempt(service)
    try:
        await scripted_model.request()
        await scripted_model.request()
        restored = await saved_state()
        current = restored.envd_sessions["workspace"].descriptor
        assert (current.session_id == original.session_id) is (lost_cleanup and not successor)
        assert current.generation == original.generation
        assert (laptop.workspace / "proof.txt").read_text() == "first\nsecond\n"
    finally:
        gate.set()
        await second
    assert (await runs_kit.get_run(service, run_id))["status"] == "completed"
    assert (await saved_state()).envd_sessions == {}
