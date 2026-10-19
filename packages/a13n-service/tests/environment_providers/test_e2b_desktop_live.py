"""Opt-in real E2B desktop input and lifecycle acceptance; owns exactly one sandbox."""

# ruff: noqa: RUF001 -- Fullwidth punctuation is intentional Unicode input coverage.

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

import pytest
from a13n_harness.environment.computer import (
    ComputerClick,
    ComputerDrag,
    ComputerMove,
    ComputerPoint,
    ComputerPressKeys,
    ComputerScroll,
    ComputerTypeText,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.e2b.connection import close_sandbox, open_sandbox
from a13n_harness.providers.environment.e2b.http import http_execution
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime
from a13n_service.providers.environments.e2b.configuration import E2BRecipe
from a13n_service.providers.environments.e2b.management import E2BManagement
from pydantic import SecretStr

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("A13N_TEST_E2B_ENVD_TEMPLATE"), reason="Requires an Envd template"),
    pytest.mark.skipif(not os.environ.get("A13N_TEST_E2B_API_KEY"), reason="Requires A13N_TEST_E2B_API_KEY"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def guest_command(sandbox, command, **kwargs):
    """Fixture setup and verification only; Agent operations use EIP."""
    return await sandbox.commands.run(
        command,
        user="sandbox",
        envs={"DISPLAY": ":99", "XAUTHORITY": "/run/a13n-desktop/Xauthority", "HOME": "/home/sandbox"},
        **kwargs,
    )


async def test_envd_desktop_actions_reconnect_and_pause_resume():
    recipe = E2BRecipe(
        execution="envd_http",
        desktop=True,
        template=os.environ["A13N_TEST_E2B_ENVD_TEMPLATE"],
        timeout_seconds=600,
        request_timeout_seconds=60,
    )
    runtime = E2BProviderRuntime(SecretStr(os.environ["A13N_TEST_E2B_API_KEY"]))
    identity = "env-desktop-" + secrets.token_hex(8)
    management = E2BManagement(recipe, environment_id=identity, state=None, runtime=runtime, operation_id="op-create")
    execution = None
    sandbox = None
    try:
        await management.create()
        assert management.state is not None
        original_state = management.state
        execution = http_execution(management)
        await execution.open(execution_id="exec-desktop-first")
        await execution.check_ready(frozenset({"computer"}))
        computer = execution.operations.computer
        assert computer is not None
        sandbox = await open_sandbox(management._state.sandbox_id, management._options(), management._validate_target)
        await sandbox.files.write(
            "/tmp/a13n-input.sh",
            "#!/bin/bash\nIFS= read -r line\nprintf '%s' \"$line\" > /tmp/a13n-input.txt\nsleep 120\n",
        )
        await guest_command(
            sandbox,
            "xterm -T a13n-desktop-input -e bash /tmp/a13n-input.sh",
            background=True,
            timeout=0,
        )
        async with asyncio.timeout(30):
            while True:
                try:
                    window = await guest_command(
                        sandbox, "xdotool search --onlyvisible --name '^a13n-desktop-input$'", request_timeout=5
                    )
                    identifier = int(window.stdout.splitlines()[0])
                    break
                except Exception:
                    await asyncio.sleep(0.2)
        await guest_command(sandbox, f"xdotool windowactivate --sync {identifier}")
        geometry = await guest_command(sandbox, f"xdotool getwindowgeometry --shell {identifier}")
        values = dict(line.split("=", 1) for line in geometry.stdout.splitlines())
        before = await computer.observe(max_dimension=1280)
        point = ComputerPoint(
            x=int(values["X"]) + int(values["WIDTH"]) // 2, y=int(values["Y"]) + int(values["HEIGHT"]) // 2
        )
        for request in (
            ComputerClick(observation=before.observation, point=point),
            ComputerPressKeys(keys=("a",)),
            ComputerPressKeys(keys=("enter",)),
        ):
            result = await computer.execute(request)
            assert result.effect == "executed" and result.input_cleanup_complete
        async with asyncio.timeout(15):
            while True:
                try:
                    text = await sandbox.files.read("/tmp/a13n-input.txt")
                    break
                except Exception:
                    await asyncio.sleep(0.2)
        assert text == "a"
        with pytest.raises(EnvironmentError) as unsupported:
            await computer.execute(ComputerTypeText(text="中文\ntext"))
        assert unsupported.value.code == "environment_unsupported"
        after = await computer.observe()
        assert before.data != after.data
        scaled = await computer.observe(max_dimension=640)
        assert (scaled.observation.width, scaled.observation.height) == (640, 400)
        for request in (
            ComputerMove(observation=scaled.observation, point=ComputerPoint(x=10, y=10)),
            ComputerDrag(
                observation=scaled.observation,
                start=ComputerPoint(x=10, y=10),
                end=ComputerPoint(x=40, y=40),
                duration_ms=300,
            ),
            ComputerScroll(
                observation=scaled.observation,
                point=ComputerPoint(x=40, y=40),
                delta_y=-1,
                unit="steps",
            ),
        ):
            result = await computer.execute(request)
            assert result.effect == "executed" and result.input_cleanup_complete
        position = await guest_command(sandbox, "xdotool getmouselocation --shell")
        coordinates = dict(line.split("=", 1) for line in position.stdout.splitlines())
        assert (int(coordinates["X"]), int(coordinates["Y"])) == (80, 80)
        await execution.close()
        execution = None
        await management.stop()
        assert await management.inspect() == "stopped"
        await management.start()
        assert management.state == original_state
        execution = http_execution(management)
        await execution.open(execution_id="exec-desktop-resumed")
        computer = execution.operations.computer
        assert computer is not None
        assert (await computer.describe()).input_ready
        with pytest.raises(EnvironmentError):
            await computer.execute(ComputerClick(observation=before.observation, point=point))
        assert (await computer.observe()).data
        assert (await computer.execute(ComputerPressKeys(keys=("escape",)))).effect == "executed"
    finally:
        try:
            if sandbox is not None:
                await close_sandbox(sandbox)
            if execution is not None:
                await execution.close()
        finally:
            if management.state is not None:
                await management.destroy()
                assert await management.inspect() == "absent"


async def test_desktop_editor_chinese_font_display_and_keyboard_edit(tmp_path: Path):
    recipe = E2BRecipe(
        execution="envd_http",
        desktop=True,
        template=os.environ["A13N_TEST_E2B_ENVD_TEMPLATE"],
        timeout_seconds=300,
        request_timeout_seconds=60,
    )
    runtime = E2BProviderRuntime(SecretStr(os.environ["A13N_TEST_E2B_API_KEY"]))
    identity = "env-text-" + secrets.token_hex(8)
    management = E2BManagement(recipe, environment_id=identity, state=None, runtime=runtime, operation_id="op-create")
    execution = None
    sandbox = None
    try:
        await management.create()
        execution = http_execution(management)
        await execution.open(execution_id="exec-text")
        computer = execution.operations.computer
        assert computer is not None
        sandbox = await open_sandbox(management._state.sandbox_id, management._options(), management._validate_target)
        fonts = await guest_command(sandbox, "fc-list :lang=zh family", timeout=10)
        assert "Noto Sans CJK" in fonts.stdout
        (tmp_path / "fonts.txt").write_text(fonts.stdout)
        path = "/workspace/a13n-text-acceptance.txt"
        text = "E2B Envd 中文显示验收\n\n第一行：春夏秋冬。\nSecond line\n"
        await sandbox.files.write(path, text, user="sandbox")
        await guest_command(sandbox, f"mousepad {path}", background=True, timeout=0)
        async with asyncio.timeout(30):
            while True:
                try:
                    await guest_command(sandbox, "xdotool search --onlyvisible --class Mousepad windowactivate --sync")
                    break
                except Exception:
                    await asyncio.sleep(0.2)
        for request in (
            ComputerPressKeys(keys=("control", "end")),
            ComputerPressKeys(keys=("a",)),
            ComputerPressKeys(keys=("enter",)),
            ComputerPressKeys(keys=("control", "s")),
        ):
            assert (await computer.execute(request)).effect == "executed"
        expected = text + "a\n"
        async with asyncio.timeout(10):
            while await sandbox.files.read(path) != expected:
                await asyncio.sleep(0.2)
        (tmp_path / "edited.txt").write_text(expected)
        screenshot = await computer.observe(max_dimension=1280)
        (tmp_path / "chinese-editor.jpg").write_bytes(screenshot.data)
        print(f"Desktop text/font evidence: {tmp_path}")
    finally:
        try:
            if sandbox is not None:
                await close_sandbox(sandbox)
            if execution is not None:
                await execution.close()
        finally:
            if management.state is not None:
                await management.destroy()
                assert await management.inspect() == "absent"
