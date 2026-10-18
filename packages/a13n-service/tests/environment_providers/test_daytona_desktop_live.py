"""Opt-in Daytona desktop acceptance; the test owns and deletes its sandbox."""

# ruff: noqa: RUF001 -- Fullwidth punctuation exercises real Unicode keyboard input.

import asyncio
import os
import secrets
import shlex
from pathlib import PurePosixPath

import pytest
from a13n_harness.environment.computer import (
    ComputerClick,
    ComputerDrag,
    ComputerPoint,
    ComputerPressKeys,
    ComputerScroll,
    ComputerTypeText,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.daytona.execution import DaytonaExecution
from a13n_harness.providers.environment.daytona.shared import (
    DaytonaConnectionConfiguration,
)
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_service.providers.environments.daytona.configuration import DaytonaRecipe
from a13n_service.providers.environments.daytona.management import DaytonaManagement

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_TEST_DAYTONA_DESKTOP") != "1", reason="Requires Daytona desktop opt-in"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_daytona_desktop_unicode_and_resume(tmp_path):
    try:
        runtime = NativeRuntime(
            DaytonaConnectionConfiguration.model_validate_json(os.environ["A13N_TEST_DAYTONA_BACKEND_JSON"]),
            TokenCredential.model_validate_json(os.environ["A13N_TEST_DAYTONA_CREDENTIAL_JSON"]),
        )
    except (KeyError, ValueError):
        pytest.fail("Configure the Daytona test backend and credential JSON", pytrace=False)
    recipe = DaytonaRecipe(desktop=True, snapshot=os.environ.get("A13N_TEST_DAYTONA_SNAPSHOT"))
    identity = "env-daytona-desktop-" + secrets.token_hex(8)
    management = DaytonaManagement(recipe, identity, None, runtime, "op-create")
    execution = None
    try:
        await management.create()
        initial = management.state
        assert initial is not None
        execution = DaytonaExecution(recipe, identity, initial, runtime)
        await execution.open(execution_id="exec-daytona-first")
        computer = execution.operations.computer
        assert computer is not None
        root = PurePosixPath(recipe.root)
        # The fixture launches a real terminal; only Computer input writes its output.
        fixture = f"cat > {shlex.quote(str(root / 'desktop-input.txt'))}; sleep 300"
        await execution.execute(
            [
                "sh",
                "-c",
                "nohup env DISPLAY=:0 LC_ALL=C.UTF-8 xfce4-terminal --disable-server --geometry=100x25+20+30 --title=a13n-desktop-acceptance "
                + "--command="
                + shlex.quote("sh -c " + shlex.quote(fixture))
                + " >/tmp/a13n-desktop-acceptance.log 2>&1 </dev/null &",
            ],
            30,
        )
        assert execution.toolbox is not None
        async with asyncio.timeout(30):
            while True:
                windows = await execution.toolbox.request(
                    "GET", execution.target.target_id + "/computeruse/display/windows"
                )
                assert isinstance(windows, dict)
                window = next((w for w in windows["windows"] if w.get("title") == "a13n-desktop-acceptance"), None)
                if window is not None:
                    break
                await asyncio.sleep(0.2)
        screen = await computer.observe()
        # The fixture explicitly positions its 100x25 terminal at +20+30.
        # Daytona window metadata may report 1x1 for a visible terminal.
        point = ComputerPoint(x=200, y=200)
        await computer.execute(ComputerClick(observation=screen.observation, point=point))
        text = "中文输入验证：你好，世界！\r\n\r\n第二行：Daytona 123\r第三行：换行成功。\n"
        await computer.execute(ComputerTypeText(text=text))
        await computer.execute(ComputerPressKeys(keys=("ctrl", "d")))
        expected = text.replace("\r\n", "\n").replace("\r", "\n")
        (tmp_path / "daytona-desktop.jpg").write_bytes((await computer.observe()).data)
        actual = (await execution.operations.files.read_text("/desktop-input.txt")).text
        assert actual == expected
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
        await execution.close()
        execution = None
        await management.stop()
        assert await management.inspect() == "stopped"
        await management.start()
        assert management.state == initial
        execution = DaytonaExecution(recipe, identity, initial, runtime)
        await execution.open(execution_id="exec-daytona-resumed")
        assert (await execution.operations.files.read_text("/desktop-input.txt")).text == expected
        computer = execution.operations.computer
        assert computer is not None
        assert (await computer.describe()).input_ready
        with pytest.raises(EnvironmentError):
            await computer.execute(ComputerClick(observation=screen.observation, point=point))
    finally:
        try:
            if execution is not None:
                await execution.close()
        finally:
            try:
                # Reconcile by the owned name even if create lost its response.
                await management.destroy()
            finally:
                await management.close()
