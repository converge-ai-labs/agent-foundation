"""Opt-in shared Envd clipboard/handoff acceptance on one owned E2B or Daytona sandbox.

Templates must contain current amd64 Envd plus the GTK/Xlib acceptance dependencies.
Credentials stay in environment variables. No personal desktop or hosted model is used.
"""

import asyncio
import json
import os
import shlex
from pathlib import Path
from uuid import uuid4

import pytest
from a13n_service.providers.environments.daytona import DAYTONA
from a13n_service.providers.environments.daytona.management import DaytonaManagement
from a13n_service.providers.environments.e2b.definition import E2B
from a13n_service.runs.environments.maintenance import maintain_environments
from a13n_service.runs.environments.schemas import Handle

from ..environments_support import environment, reserve, stored
from . import test_docker_clipboard_live as acceptance

PROVIDER = os.environ.get("A13N_TEST_CLIPBOARD_CLOUD")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.timeout(900),
    pytest.mark.skipif(PROVIDER not in {"e2b", "daytona"}, reason="Requires explicit cloud clipboard opt-in"),
]


@pytest.fixture
async def desktop(service, runs_kit, tmp_path):
    from a13n_harness.providers.environment.daytona.shared import DaytonaConnectionConfiguration
    from a13n_harness.providers.environment.e2b.connection import close_sandbox, open_sandbox
    from a13n_harness.providers.environment.native.configuration import TokenCredential
    from a13n_harness.providers.environment.native.environment import NativeRuntime
    from a13n_service.providers.environments.daytona.configuration import DaytonaRecipe

    await runs_kit.pause_sweeps(service)
    if PROVIDER == "e2b":
        definition = E2B
        config = {}
        credential = {"api_key": os.environ["A13N_TEST_E2B_API_KEY"]}
        recipe = {
            "template": os.environ["A13N_TEST_E2B_ENVD_TEMPLATE"],
            "execution": "envd_http",
            "timeout_seconds": 900,
        }
    else:
        definition = DAYTONA
        config = json.loads(os.environ["A13N_TEST_DAYTONA_BACKEND_JSON"])
        credential = json.loads(os.environ["A13N_TEST_DAYTONA_CREDENTIAL_JSON"])
        recipe = {"snapshot": os.environ["A13N_TEST_DAYTONA_SNAPSHOT"]}
    recipe.update(desktop=True, desktop_assistance=True, request_timeout_seconds=120)
    sandbox = management = None
    env_id = None
    try:
        response = await service.client.post(
            f"{service.api}/environment-providers",
            json={"type": PROVIDER, "name": "Cloud clipboard acceptance", "config": config, "credential": credential},
        )
        assert response.status_code == 201, "Cloud provider creation failed"
        template = await service.client.post(
            f"{service.api}/environment-templates",
            json={"name": "Cloud clipboard", "provider_id": response.json()["id"], "config": {"recipe": recipe}},
        )
        assert template.status_code == 201, "Cloud template validation failed"
        instance = await reserve(service, template.json()["id"])
        env_id = instance["id"]
        # Retain only the owned resource identity for cleanup after unexpected process termination.
        (tmp_path / "owned.json").write_text(json.dumps({"provider": PROVIDER, "environment_id": env_id}))
        async with asyncio.timeout(240):
            while True:
                await maintain_environments(service.runtime, owner="cloud-clipboard-test")
                state = await environment(service, env_id)
                if state["status"] == "ready":
                    break
                assert state["status"] in {"pending", "creating"}, state["status"]
                await asyncio.sleep(0.5)
        print(f"{PROVIDER}: owned cloud sandbox ready")
        handle = Handle.model_validate((await stored(service, env_id)).handle)
        connector = definition.execution.execution_connector(
            definition.execution_environment(handle.recipe),
            configuration=config,
            credential=credential,
            environment_id=env_id,
            state=handle.state,
        )
        if PROVIDER == "e2b":
            sandbox = await open_sandbox(
                handle.state.state["sandbox_id"], {"api_key": credential["api_key"]}, lambda _: None
            )

            async def guest(script):
                result = await sandbox.commands.run(script, user="root", timeout=30, request_timeout=60)
                assert result.exit_code == 0, "E2B fixture command failed"
                return result.stdout
        else:
            runtime = NativeRuntime(
                DaytonaConnectionConfiguration.model_validate(config), TokenCredential.model_validate(credential)
            )
            management = DaytonaManagement(
                DaytonaRecipe.model_validate(recipe), env_id, handle.state, runtime, "op-clipboard-fixture"
            )
            target = await management.lookup()
            assert target is not None
            toolbox = await management.open_toolbox(target)

            async def guest(script):
                result = await toolbox.request(
                    "POST",
                    target.id + "/process/execute",
                    body={"command": shlex.join(["sh", "-c", script]), "timeout": 30},
                )
                assert result["exitCode"] == 0, "Daytona fixture command failed"
                return result["result"]

        digest = (await guest("sha256sum /usr/local/bin/a13n-envd")).split()[0]
        expected_digest = os.environ.get("A13N_TEST_ENVD_SHA256")
        if expected_digest:
            assert digest == expected_digest, "Cloud Envd binary differs from the acceptance build"
        (tmp_path / "binary-sha256.txt").write_text(digest)
        source = Path(__file__).resolve().parents[4] / "dev/fixtures/linux-desktop"
        # Setup uses Files; subsequent input is exclusively clipboard/keys/VNC.
        async with await connector.open() as setup:
            for name in ("paste.py", "clipboard-owner.py"):
                await setup.operations.files.write_text("/tmp/" + name, (source / name).read_text(), mode="create")
        await guest(
            "runuser -u sandbox -- env DISPLAY=:99 XAUTHORITY=/run/a13n-desktop/Xauthority "
            "sh -c 'nohup /usr/bin/python3 /tmp/paste.py >/tmp/clipboard-fixture.log 2>&1 </dev/null &'"
        )

        async def read():
            result = await guest("cat /workspace/paste-state.json 2>/dev/null || printf '{}'")
            return json.loads(result)

        async def command(action):
            payload = json.dumps({"action": action, "id": uuid4().hex})
            code = "from pathlib import Path;Path('/workspace/paste-command.json').write_text(" + repr(payload) + ")"
            await guest(shlex.join(["runuser", "-u", "sandbox", "--", "python3", "-c", code]))

        await acceptance.eventually(read, lambda v: v.get("ready"))
        yield env_id, connector, read, command
    finally:
        try:
            if sandbox is not None:
                await close_sandbox(sandbox)
            if management is not None:
                await management.close()
        finally:
            if env_id is not None:
                raw_handle = (await stored(service, env_id)).handle
                handle = Handle.model_validate(raw_handle) if raw_handle else None
                async with await definition.open_provider(configuration=config, credential=credential) as owner:
                    for attempt in range(3):
                        try:
                            await owner.destroy(
                                definition.validate_environment(recipe),
                                environment_id=env_id,
                                state=handle.state if handle else None,
                                operation_id="op-clipboard-cleanup",
                            )
                            assert (
                                await owner.inspect(
                                    definition.validate_environment(recipe),
                                    environment_id=env_id,
                                    state=handle.state if handle else None,
                                )
                            ).status == "absent"
                            (tmp_path / "cleanup.json").write_text(
                                json.dumps({"provider": PROVIDER, "environment_id": env_id, "status": "absent"})
                            )
                            (tmp_path / "owned.json").unlink()
                            break
                        except Exception:
                            if attempt == 2:
                                raise
                            await asyncio.sleep(1)


async def test_cloud_clipboard_and_human_handoff(service, scripted_model, runs_kit, desktop, tmp_path):
    await acceptance.test_clipboard_read_write_lifetime_and_replacement(desktop)
    print(f"{PROVIDER}: clipboard read/write and ownership verified")
    _, connector, read, command = desktop
    await command("reset")
    await acceptance.eventually(read, lambda v: v["text"] == "")
    await acceptance.test_clipboard_human_control_and_agent_resume(service, scripted_model, runs_kit, desktop)
    print(f"{PROVIDER}: human handoff, revocation and Agent continuation verified")
    observed = await read()
    evidence = {
        "provider": PROVIDER,
        "envd_sha256": (tmp_path / "binary-sha256.txt").read_text(),
        "clipboard_read_write": True,
        "human_handoff_and_resume": True,
        "widget_text": observed["text"],
    }
    (tmp_path / "acceptance.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    async with await connector.open() as final:
        screenshot = await final.operations.computer.observe()
        (tmp_path / "desktop.jpg").write_bytes(screenshot.data)
    print(f"Cloud clipboard evidence: {tmp_path}")
