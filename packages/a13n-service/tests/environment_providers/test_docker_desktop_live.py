"""Opt-in managed desktop test; owns all containers, never uses the user's desktop."""

import asyncio
import os
import re
from io import BytesIO

import pytest
from a13n_harness.environment.models import COMPUTER_ACTIONS, EnvironmentPermissionSet
from a13n_service.runs.environments.maintenance import maintain_environments
from PIL import Image, ImageChops

from ..environments_support import environment, reserve, stored
from .test_computer_x11 import returned

IMAGE = os.environ.get("A13N_TEST_DOCKER_DESKTOP_IMAGE")
pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not IMAGE, reason="Requires owned Docker desktop image")]


@pytest.fixture(params=["configured", "tcp"])
async def docker_endpoint(request):
    endpoint = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if request.param == "configured":
        yield endpoint
        return
    if not endpoint.startswith("unix://"):
        pytest.skip("TCP relay scenario requires a local Unix Engine socket")
    tasks = set()

    async def forward(reader, writer):
        tasks.add(asyncio.current_task())
        remote_writer = None
        pumps = []
        try:
            remote_reader, remote_writer = await asyncio.open_unix_connection(endpoint.removeprefix("unix://"))

            async def copy(source, destination):
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()

            pumps = [asyncio.create_task(copy(reader, remote_writer)), asyncio.create_task(copy(remote_reader, writer))]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            writer.close()
            if remote_writer:
                remote_writer.close()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(forward, "127.0.0.1", 0)
    try:
        yield f"tcp://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()


async def test_template_creates_desktop_run_input_reconnect_and_restart(
    service, scripted_model, runs_kit, docker_endpoint
):
    import docker

    await runs_kit.pause_sweeps(service)
    client = service.client
    provider = await client.post(
        f"{service.api}/environment-providers",
        json={
            "type": "docker",
            "name": "Desktop",
            "config": {
                **({} if docker_endpoint.startswith("unix://") else {"docker_host": docker_endpoint}),
                "envd_http": {"host": "127.0.0.1"},
            },
        },
    )
    assert provider.status_code == 201, provider.text
    template = await client.post(
        f"{service.api}/environment-templates",
        json={
            "name": "Linux desktop",
            "provider_id": provider.json()["id"],
            "config": {"recipe": {"image": IMAGE, "desktop": True, "execution": "envd_http", "pull_policy": "never"}},
        },
    )
    assert template.status_code == 201, template.text
    instance = await reserve(service, template.json()["id"])
    env_id = instance["id"]
    engine = await asyncio.to_thread(docker.DockerClient, base_url=docker_endpoint)
    container = None
    task = None
    try:
        await maintain_environments(service.runtime, owner="desktop-test")
        state = await environment(service, env_id)
        assert state["status"] == "ready", state
        row = await stored(service, env_id)
        # Endpoint ports are inspected on the exact container; tokens never enter public state.
        container = await asyncio.to_thread(engine.containers.get, "a13n-" + env_id)
        container_id = container.id
        assert container.attrs["NetworkSettings"]["Ports"]["8888/tcp"][0]["HostIp"] == "127.0.0.1"
        assert container.attrs["HostConfig"]["NetworkMode"] == "bridge"
        assert "http-token" not in str(row.handle)
        checked = await client.post(f"{service.api}/environments/{env_id}/computer/check")
        assert checked.status_code == 200, checked.text
        assert checked.json()["observe_ready"] and checked.json()["input_ready"]

        # An independent native X11 window acknowledges keyboard input by exiting.
        app = await asyncio.to_thread(
            engine.api.exec_create,
            container_id,
            [
                "runuser",
                "-u",
                "sandbox",
                "--",
                "env",
                "DISPLAY=:99",
                "XAUTHORITY=/run/a13n-desktop/Xauthority",
                "xmessage",
                "-title",
                "Desktop validation",
                "-geometry",
                "400x200+50+50",
                "-bg",
                "red",
                "-buttons",
                "Ready:0",
                "-default",
                "Ready",
                "Press Enter",
            ],
        )
        await asyncio.to_thread(engine.api.exec_start, app["Id"], detach=True)
        async with asyncio.timeout(10):
            while True:
                windows = await asyncio.to_thread(
                    container.exec_run,
                    ["env", "DISPLAY=:99", "XAUTHORITY=/run/a13n-desktop/Xauthority", "xwininfo", "-root", "-tree"],
                )
                if b"Desktop validation" in windows.output:
                    geometry = await asyncio.to_thread(
                        container.exec_run,
                        [
                            "env",
                            "DISPLAY=:99",
                            "XAUTHORITY=/run/a13n-desktop/Xauthority",
                            "xwininfo",
                            "-name",
                            "Desktop validation",
                        ],
                    )
                    description = geometry.output.decode()
                    identifier = re.search(r"Window id: (0x[0-9a-f]+)", description)
                    managed = await asyncio.to_thread(
                        container.exec_run,
                        [
                            "env",
                            "DISPLAY=:99",
                            "XAUTHORITY=/run/a13n-desktop/Xauthority",
                            "xprop",
                            "-root",
                            "_NET_CLIENT_LIST",
                        ],
                    )
                    if identifier and identifier[1].encode() in managed.output and "IsViewable" in description:
                        x = int(re.search(r"Absolute upper-left X:\s+(-?\d+)", description)[1])
                        y = int(re.search(r"Absolute upper-left Y:\s+(-?\d+)", description)[1])
                        width = int(re.search(r"Width:\s+(\d+)", description)[1])
                        height = int(re.search(r"Height:\s+(\d+)", description)[1])
                        point = {"x": x + width // 2, "y": y + height // 2}
                        break
                await asyncio.sleep(0.05)
        agent = await runs_kit.create_agent(service, scripted_model, toolsets={"computer": {"enabled": True}})
        submitted = await runs_kit.start_thread(
            service,
            agent,
            "Use the desktop",
            environments=[
                {
                    "name": "workspace",
                    "environment_id": env_id,
                    "permission_ceiling": EnvironmentPermissionSet(operations=COMPUTER_ACTIONS).model_dump(mode="json"),
                }
            ],
        )
        scripted_model.call("computer_observe", {"max_dimension": 1280}, call_id="before")
        task = await runs_kit.attempt(service)
        await scripted_model.request()
        observed = returned(await scripted_model.request(), "before")
        assert observed["ok"], observed
        scripted_model.call(
            "computer_click",
            {"observation_id": observed["observation_id"], "point": point},
            call_id="focus",
        )
        assert returned(await scripted_model.request(), "focus")["effect"] == "executed"
        scripted_model.call("computer_press_keys", {"keys": ["enter"]}, call_id="key")
        key_result = returned(await scripted_model.request(), "key")
        assert key_result.get("effect") == "executed", key_result
        async with asyncio.timeout(10):
            while (await asyncio.to_thread(engine.api.exec_inspect, app["Id"]))["Running"]:
                await asyncio.sleep(0.05)
        assert (await asyncio.to_thread(engine.api.exec_inspect, app["Id"]))["ExitCode"] == 0
        scripted_model.call("computer_observe", {"max_dimension": 1280}, call_id="after")
        assert returned(await scripted_model.request(), "after")["ok"]
        scripted_model.say("Done")
        await task
        run_id = submitted["run"]["id"]
        assert (await runs_kit.get_run(service, run_id))["status"] == "completed"
        items = await runs_kit.items(service, run_id)
        images = {}
        for item in items["items"]:
            content = item["content"]
            if content.get("tool_images"):
                response = await client.get(f"{service.api}/runs/{run_id}/images/{content['tool_images'][0]['id']}")
                assert response.status_code == 200
                images[content["toolCallId"]] = Image.open(BytesIO(response.content)).convert("RGB")
        assert images["before"].size == (1280, 800)
        assert ImageChops.difference(images["before"], images["after"]).getbbox() is not None

        # Each check makes a new execution connection; closing the previous Run did not destroy it.
        assert (await client.post(f"{service.api}/environments/{env_id}/computer/check")).json()["observe_ready"]
        await asyncio.to_thread(container.exec_run, ["touch", "/workspace/preserved"])
        # Explicit stop/start retains the exact container and files, but creates a fresh GUI session.
        from a13n_service.providers.environments.docker.definition import DOCKER

        recipe = template.json()["config"]["recipe"]
        from a13n_harness.environment.models import EnvironmentState

        target_state = EnvironmentState.model_validate(row.handle["state"])
        async with await DOCKER.open_provider(
            configuration={"docker_host": docker_endpoint, "envd_http": {"host": "127.0.0.1"}}
        ) as management:
            for _ in range(3):
                await management.stop(recipe, environment_id=env_id, state=target_state, operation_id="stop")
                await management.start(recipe, environment_id=env_id, state=target_state, operation_id="start")
        await asyncio.to_thread(container.reload)
        assert container.id == container_id
        assert (await asyncio.to_thread(container.exec_run, ["test", "-f", "/workspace/preserved"])).exit_code == 0
        assert (await client.post(f"{service.api}/environments/{env_id}/computer/check")).json()["input_ready"]
    except BaseException:
        if container is not None:
            print((await asyncio.to_thread(container.logs, tail=15)).decode(errors="replace"))
        raise
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if container is None:
            try:
                container = await asyncio.to_thread(engine.containers.get, "a13n-" + env_id)
            except docker.errors.NotFound:
                pass
        if container is not None:
            await asyncio.to_thread(container.remove, force=True)
        await asyncio.to_thread(engine.close)


async def test_http_authentication_identity_and_unreachable_endpoint(docker_endpoint):
    """Two actual daemons must not accept one another's token or device binding."""
    from uuid import uuid4

    import docker
    from a13n_harness.environment.errors import EnvironmentProviderError
    from a13n_harness.providers.environment.remote_envd.configuration import (
        HttpEnvdConnectionConfiguration,
        HttpEnvdCredential,
    )
    from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
    from a13n_service.providers.environments.docker.definition import DOCKER
    from pydantic import SecretStr

    engine = await asyncio.to_thread(docker.DockerClient, base_url=docker_endpoint)
    containers = []
    recipe = {"image": IMAGE, "execution": "envd_http", "desktop": False, "pull_policy": "never"}
    async with await DOCKER.open_provider(
        configuration={"docker_host": docker_endpoint, "envd_http": {"host": "127.0.0.1"}}
    ) as management:
        try:
            credentials = []
            endpoints = []
            identities = []
            for _ in range(2):
                identity = "env_" + uuid4().hex
                identities.append(identity)
                # Create through the real Service management adapter, including EIP readiness.
                await management.create(recipe, environment_id=identity, operation_id="create")
                container = await asyncio.to_thread(engine.containers.get, "a13n-" + identity)
                containers.append(container)
                port = container.attrs["NetworkSettings"]["Ports"]["8888/tcp"][0]["HostPort"]
                endpoints.append(f"http://127.0.0.1:{port}")
                result = await asyncio.to_thread(container.exec_run, ["cat", "/var/lib/a13n-envd/http-token"])
                credentials.append(HttpEnvdCredential(token=SecretStr(result.output.decode())))
            assert endpoints[0] != endpoints[1]
            assert credentials[0].token != credentials[1].token
            async with HttpEnvdProviderRuntime(
                HttpEnvdConnectionConfiguration(endpoint=endpoints[0]), credentials[1]
            ) as runtime:
                with pytest.raises(EnvironmentProviderError) as rejected:
                    await runtime.describe(expected_device_id=identities[0])
                assert rejected.value.code == "provider_connection_failed"
            async with HttpEnvdProviderRuntime(
                HttpEnvdConnectionConfiguration(endpoint=endpoints[0]), credentials[0]
            ) as runtime:
                with pytest.raises(EnvironmentProviderError) as rejected:
                    await runtime.describe(expected_device_id=identities[1])
                assert rejected.value.code == "provider_device_mismatch"
            await asyncio.to_thread(containers[0].stop, timeout=2)
            async with HttpEnvdProviderRuntime(
                HttpEnvdConnectionConfiguration(endpoint=endpoints[0], initialization_timeout=1, request_timeout=1),
                credentials[0],
            ) as runtime:
                with pytest.raises(EnvironmentProviderError) as rejected:
                    await runtime.describe(expected_device_id=identities[0])
                assert rejected.value.code in {"provider_connection_failed", "provider_connection_timeout"}
        finally:
            for container in containers:
                await asyncio.to_thread(container.remove, force=True)
            await asyncio.to_thread(engine.close)
