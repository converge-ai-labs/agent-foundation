"""Opt-in real Vercel EIP files, processes, Computer and stop/start acceptance."""

import asyncio
import os
import secrets

import pytest
from a13n_harness.environment.commands import CommandRequest, ShellCommand
from a13n_harness.environment.computer import (
    ComputerClick,
    ComputerDrag,
    ComputerPoint,
    ComputerPressKeys,
    ComputerScroll,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.http import HttpReference, http_execution
from a13n_harness.providers.environment.vercel.shared import (
    VercelConnectionConfiguration,
)
from a13n_service.providers.environments.vercel.configuration import VercelRecipe
from a13n_service.providers.environments.vercel.management import VercelManagement

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.timeout(900),
    pytest.mark.skipif(os.environ.get("A13N_TEST_VERCEL_DESKTOP") != "1", reason="Requires Vercel opt-in"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_vercel_envd_desktop_and_resume(tmp_path):
    try:
        runtime = NativeRuntime(
            VercelConnectionConfiguration.model_validate_json(os.environ["A13N_TEST_VERCEL_BACKEND_JSON"]),
            TokenCredential.model_validate_json(os.environ["A13N_TEST_VERCEL_CREDENTIAL_JSON"]),
        )
        recipe = VercelRecipe(
            execution="envd_http",
            desktop=True,
            snapshot=os.environ["A13N_TEST_VERCEL_SNAPSHOT"],
            request_timeout_seconds=300,
            timeout_seconds=1800,
        )
    except (KeyError, ValueError):
        pytest.fail("Configure the Vercel test backend, credential and Envd image", pytrace=False)
    identity = "env-vercel-envd-" + secrets.token_hex(8)
    management = VercelManagement(recipe, identity, None, runtime, "op-create")
    execution = None

    def connect():
        return http_execution(HttpReference(recipe, identity, management.state, runtime))

    async def shell(script):
        result = await execution.operations.shell.exec(
            CommandRequest(
                command=ShellCommand(profile_id="default", script=script),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                ),
            )
        )
        assert result.status.exit_code == 0
        return (result.output.stdout.inline or b"").decode()

    try:
        await management.create()
        initial = management.state
        execution = connect()
        await execution.open(execution_id="exec-first")
        files = execution.operations.files
        await files.write_text("/workspace/persistence.txt", "Envd persistence", mode="create")
        assert await shell("cat /workspace/persistence.txt") == "Envd persistence"
        await files.write_text(
            "/workspace/input.sh", "read line; printf '%s' \"$line\" > /workspace/input.txt; sleep 300", mode="create"
        )
        # A short fixture launch is separate from input: only Computer keys write input.txt.
        target = await management.lookup()
        assert target is not None
        await command(
            management,
            target.session.id,
            [
                "runuser",
                "-u",
                "sandbox",
                "--",
                "env",
                "DISPLAY=:99",
                "XAUTHORITY=/run/a13n-desktop/Xauthority",
                "sh",
                "-c",
                "nohup xterm -geometry 80x20+20+30 -T a13n-input -e bash /workspace/input.sh >/tmp/terminal.log 2>&1 </dev/null &",
            ],
            timeout=10,
            sudo=True,
        )
        async with asyncio.timeout(30):
            while "a13n-input" not in await shell(
                "env DISPLAY=:99 XAUTHORITY=/run/a13n-desktop/Xauthority xwininfo -root -tree"
            ):
                await asyncio.sleep(0.2)
        computer = execution.operations.computer
        assert computer is not None
        screen = await computer.observe()
        point = ComputerPoint(x=150, y=150)
        for request in (
            ComputerClick(observation=screen.observation, point=point),
            ComputerPressKeys(keys=("a",)),
            ComputerPressKeys(keys=("enter",)),
        ):
            assert (await computer.execute(request)).effect == "executed"
        async with asyncio.timeout(10):
            while "input.txt" not in await shell("ls /workspace"):
                await asyncio.sleep(0.2)
        assert (await files.read_text("/workspace/input.txt")).text == "a"
        scaled = await computer.observe(max_dimension=640)
        await computer.execute(
            ComputerDrag(
                observation=scaled.observation,
                start=ComputerPoint(x=5, y=5),
                end=ComputerPoint(x=40, y=40),
                duration_ms=100,
            )
        )
        await computer.execute(
            ComputerScroll(observation=scaled.observation, point=ComputerPoint(x=40, y=40), delta_y=1, unit="steps")
        )
        (tmp_path / "vercel-envd.png").write_bytes((await computer.observe()).data)
        await execution.close()
        execution = None
        await management.stop()
        assert await management.inspect() == "stopped"
        await management.start()
        assert management.state is not None and initial is not None
        assert management.state.state["target_id"] == initial.state["target_id"]
        execution = connect()
        await execution.open(execution_id="exec-resumed")
        assert (await execution.operations.files.read_text("/workspace/persistence.txt")).text == "Envd persistence"
        computer = execution.operations.computer
        assert computer is not None and (await computer.describe()).input_ready
        with pytest.raises(EnvironmentError):
            await computer.execute(ComputerClick(observation=screen.observation, point=point))
    finally:
        try:
            if execution is not None:
                await execution.close()
        finally:
            try:
                await management.destroy()
            finally:
                await management.close()
