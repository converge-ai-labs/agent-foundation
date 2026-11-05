"""Outbound recipes and private bootstrap do not require an Envd HTTP route."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime
from a13n_service.distribution import OSS
from a13n_service.providers.environment.recipes.e2b import E2BRecipe
from a13n_service.providers.environment.websocket import WebSocketBootstrap
from a13n_service.providers.environments.e2b.management import E2BManagement
from a13n_service.providers.registry import Registry
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


def bootstrap():
    async def ready():
        pass

    return WebSocketBootstrap("env_test", "wss://control.example.test/connect", SecretStr("private-test-token"), ready)


@pytest.mark.parametrize(
    "provider,recipe",
    [
        ("docker", {}),
        ("e2b", {"template": "team-envd"}),
        ("daytona", {"snapshot": "team-envd"}),
        ("modal", {"image": "team-envd"}),
        ("vercel", {"snapshot": "snap_test"}),
        ("sprites", {"desktop": True}),
    ],
)
def test_managed_recipes_describe_outbound_execution(provider, recipe):
    definition = Registry.of(OSS.providers).get("environment", provider)
    config = definition.validate_environment({**recipe, "execution": "envd_ws"})
    assert definition.execution_environment(config.model_dump(mode="json")).execution == "envd_ws"
    assert "processes" in definition.describe_environment(config).operation_families


def test_standalone_connector_rejects_outbound_without_silently_using_native():
    from a13n_harness.providers.environment.docker.provider import DOCKER

    with pytest.raises(EnvironmentProviderError) as failed:
        from a13n_harness.providers.environment.docker.shared import _fingerprint

        recipe = DOCKER.validate_environment({"execution": "envd_ws"})
        DOCKER.execution_connector(
            recipe,
            environment_id="env_test",
            state=EnvironmentState(
                provider_key="docker",
                state_version="1",
                state={
                    "environment_id": "env_test",
                    "container_id": "selected-container",
                    "configuration_fingerprint": _fingerprint(recipe),
                },
            ),
        )
    assert failed.value.code == "envd_ws_requires_host_connection"


async def test_e2b_outbound_uses_private_bootstrap_without_http_probe():
    manager = E2BManagement(
        E2BRecipe(execution="envd_ws", template="team-envd"),
        environment_id="env_test",
        state=None,
        runtime=E2BProviderRuntime(SecretStr("test-key")),
        operation_id="create",
    )
    manager.websocket = bootstrap()
    run = AsyncMock()
    await manager._bootstrap_envd(SimpleNamespace(commands=SimpleNamespace(run=run)))
    run.assert_awaited_once()
    values = run.call_args.kwargs["envs"]
    assert values["A13N_ENVD_TRANSPORT"] == "reverse_websocket"
    assert values["A13N_ENVD_DEVICE_ID"] == "env_test"
    assert values["A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN"] == "private-test-token"
    assert not any(key.startswith("A13N_ENVD_HTTP_") for key in values)
    assert "private-test-token" not in str(run.call_args.args)
    assert "private-test-token" not in repr(manager.websocket)


def test_entrypoint_persists_private_token_and_removes_bootstrap_environment(tmp_path):
    entrypoint = Path(__file__).resolve().parents[4] / "deploy/docker/images/sandbox/entrypoint.sh"
    init = tmp_path / "a13n-sandbox-init"
    init.write_text("#!/bin/sh\nexit 0\n")
    init.chmod(0o700)
    credential = tmp_path / "state/token"
    values = bootstrap().environment({})
    values["A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE"] = str(credential)
    env = {**os.environ, **values, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    command = ["sh", str(entrypoint), "sh", "-c", 'test -z "${A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN+x}"']
    subprocess.run(command, env=env, check=True, capture_output=True)
    assert credential.read_text() == "private-test-token"
    assert credential.stat().st_mode & 0o777 == 0o600
    assert credential.parent.stat().st_mode & 0o777 == 0o700
    # A restart can also use the existing private credential file directly.
    del env["A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN"]
    subprocess.run(command, env=env, check=True, capture_output=True)
    assert credential.read_text() == "private-test-token"


def test_docker_outbound_preserves_entrypoint_and_publishes_no_envd_port():
    from unittest.mock import Mock

    from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine
    from a13n_service.providers.environment.recipes.docker import DockerRecipe
    from a13n_service.providers.environments.docker.management import DockerManagement

    client = Mock()
    client.images.get.return_value.id = "sha256:fixture"
    manager = DockerManagement(
        DockerRecipe(execution="envd_ws", image="test-envd"),
        "env_test",
        None,
        DockerProviderRuntime(DockerSDKEngine(client)),
    )
    manager.websocket = bootstrap()
    manager._create()
    values = client.containers.create.call_args.kwargs
    assert values["ports"] is None and values["entrypoint"] is None and values["command"] is None
    assert values["user"] == "root" and values["environment"]["A13N_ENVD_TRANSPORT"] == "reverse_websocket"
    assert values["environment"]["A13N_ENVD_DEVICE_ID"] == "env_test"


async def test_sprites_private_startup_is_delivered_on_stdin_not_argv(monkeypatch):
    from a13n_service.providers.environments.sprites import management

    from .test_sprites_desktop import manager

    manage = manager()
    manage.recipe = manage.recipe.model_copy(update={"desktop_assistance": False, "execution": "envd_ws"})
    manage.config = manage.config.model_copy(update={"execution": "envd_ws"})
    manage.websocket = bootstrap()
    run = AsyncMock()
    monkeypatch.setattr(management, "command", run)
    monkeypatch.setattr(manage, "transport", lambda: SimpleNamespace(request=AsyncMock(return_value=None)))
    await manage._ready()
    start = run.call_args_list[0]
    assert "private-test-token" in start.kwargs["stdin"]
    assert "private-test-token" not in str(start.args)
    assert "chmod 0700" in str(start.args)
    assert run.await_count == 2  # Install the private script and register its service; no HTTP probe.
