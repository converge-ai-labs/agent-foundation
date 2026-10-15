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
from a13n_harness.providers.environment.e2b.execution import E2BExecution
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime
from a13n_service.providers.environments.e2b.configuration import E2BRecipe
from a13n_service.providers.environments.e2b.management import E2BManagement
from pydantic import SecretStr

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("A13N_TEST_E2B_API_KEY"), reason="Requires A13N_TEST_E2B_API_KEY"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_native_desktop_unicode_input_reconnect_and_pause_resume():
    recipe = E2BRecipe(desktop=True, timeout_seconds=300, request_timeout_seconds=60)
    runtime = E2BProviderRuntime(SecretStr(os.environ["A13N_TEST_E2B_API_KEY"]))
    identity = "env-desktop-" + secrets.token_hex(8)
    management = E2BManagement(recipe, environment_id=identity, state=None, runtime=runtime, operation_id="op-create")
    execution = None
    try:
        await management.create()
        assert management.state is not None
        original_state = management.state
        execution = E2BExecution(recipe, environment_id=identity, state=original_state, runtime=runtime)
        await execution.open(execution_id="exec-desktop-first")
        await execution.check_ready(frozenset({"computer"}))
        computer = execution.operations.computer
        assert computer is not None
        sandbox = execution._sandbox
        assert sandbox is not None
        await sandbox.files.write(
            "/tmp/a13n-input.sh",
            "#!/bin/bash\nIFS= read -r line\nprintf '%s' \"$line\" > /tmp/a13n-input.txt\nsleep 120\n",
        )
        await sandbox.commands.run(
            "xfce4-terminal --disable-server --title=a13n-desktop-input --command='bash /tmp/a13n-input.sh'",
            background=True,
            timeout=0,
        )
        async with asyncio.timeout(30):
            while True:
                try:
                    window = await sandbox.commands.run(
                        "xdotool search --onlyvisible --name '^a13n-desktop-input$'", request_timeout=5
                    )
                    identifier = int(window.stdout.splitlines()[0])
                    break
                except Exception:
                    await asyncio.sleep(0.2)
        await sandbox.commands.run(f"xdotool windowactivate --sync {identifier}")
        geometry = await sandbox.commands.run(f"xdotool getwindowgeometry --shell {identifier}")
        values = dict(line.split("=", 1) for line in geometry.stdout.splitlines())
        before = await computer.observe(max_dimension=1280)
        point = ComputerPoint(
            x=int(values["X"]) + int(values["WIDTH"]) // 2, y=int(values["Y"]) + int(values["HEIGHT"]) // 2
        )
        for request in (
            ComputerClick(observation=before.observation, point=point),
            ComputerTypeText(text="E2B 中文输入 'quoted' $literal"),
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
        assert text == "E2B 中文输入 'quoted' $literal"
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
        position = await sandbox.commands.run("xdotool getmouselocation --shell")
        coordinates = dict(line.split("=", 1) for line in position.stdout.splitlines())
        assert (int(coordinates["X"]), int(coordinates["Y"])) == (80, 80)
        await execution.close()
        execution = None
        await management.stop()
        assert await management.inspect() == "stopped"
        await management.start()
        assert management.state == original_state
        execution = E2BExecution(recipe, environment_id=identity, state=management.state, runtime=runtime)
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
            if execution is not None:
                await execution.close()
        finally:
            if management.state is not None:
                await management.destroy()
                assert await management.inspect() == "absent"


@pytest.mark.skipif(
    not os.environ.get("A13N_TEST_E2B_DESKTOP_CJK_TEMPLATE"), reason="Requires a desktop template with Noto CJK fonts"
)
async def test_desktop_editor_multiline_chinese_and_fonts(tmp_path: Path):
    recipe = E2BRecipe(
        desktop=True,
        template=os.environ["A13N_TEST_E2B_DESKTOP_CJK_TEMPLATE"],
        timeout_seconds=300,
        request_timeout_seconds=60,
    )
    runtime = E2BProviderRuntime(SecretStr(os.environ["A13N_TEST_E2B_API_KEY"]))
    identity = "env-text-" + secrets.token_hex(8)
    management = E2BManagement(recipe, environment_id=identity, state=None, runtime=runtime, operation_id="op-create")
    execution = None
    try:
        await management.create()
        execution = E2BExecution(recipe, environment_id=identity, state=management.state, runtime=runtime)
        await execution.open(execution_id="exec-text")
        computer = execution.operations.computer
        assert computer is not None
        sandbox = execution._sandbox
        assert sandbox is not None
        fonts = await sandbox.commands.run("fc-list :lang=zh family", timeout=10)
        assert "Noto Sans CJK" in fonts.stdout
        (tmp_path / "fonts.txt").write_text(fonts.stdout)
        path = "/home/user/a13n-text-acceptance.txt"
        await sandbox.files.write(path, "")
        await sandbox.commands.run(f"mousepad {path}", background=True, timeout=0)
        async with asyncio.timeout(30):
            while True:
                try:
                    await sandbox.commands.run("xdotool search --onlyvisible --class Mousepad windowactivate --sync")
                    break
                except Exception:
                    await asyncio.sleep(0.2)
        samples = [
            "\n第一行：中文输入，不应丢字。\r\n\r\nSecond line: 'quoted' $literal $(not-a-command)\r\n末行\r\n",
            "中文输入连续测试：" + "春夏秋冬天地玄黄宇宙洪荒。" * 12 + "\n",
            "E2B Desktop — 合并后验收\n\n这份笔记通过真实桌面键盘输入。\n"
            "能力：截图、点击、中文输入、快捷键、拖拽、滚动。\n"
            "保存位置：/home/user/a13n-text-acceptance.txt\n\n"
            "Service / Worker / Harness / Official E2B Desktop SDK\n",
        ]
        for index, text in enumerate(samples):
            for request in (
                ComputerPressKeys(keys=("control", "a")),
                ComputerPressKeys(keys=("backspace",)),
                ComputerTypeText(text=text),
                ComputerPressKeys(keys=("control", "s")),
            ):
                assert (await computer.execute(request)).effect == "executed"
            expected = text.replace("\r\n", "\n").replace("\r", "\n")
            screenshot = await computer.observe(max_dimension=1280)
            (tmp_path / f"sample-{index}.jpg").write_bytes(screenshot.data)
            async with asyncio.timeout(10):
                while True:
                    actual = await sandbox.files.read(path)
                    (tmp_path / f"sample-{index}.txt").write_text(actual)
                    if actual == expected:
                        break
                    await asyncio.sleep(0.2)
            assert actual == expected
            (tmp_path / f"sample-{index}.txt").write_text(actual)
        screenshot = await computer.observe(max_dimension=1280)
        (tmp_path / "chinese-editor.jpg").write_bytes(screenshot.data)
        print(f"Desktop text/font evidence: {tmp_path}")
    finally:
        try:
            if execution is not None:
                await execution.close()
        finally:
            if management.state is not None:
                await management.destroy()
                assert await management.inspect() == "absent"
