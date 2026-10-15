"""Native desktop facet: compatibility, observation geometry and non-replayable input."""

from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
from a13n_harness.providers.environment.e2b.computer import E2BComputerOperations
from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_harness.providers.environment.e2b.desktop import Desktop, DesktopDescription, configure, run_sync
from a13n_harness.providers.environment.e2b.shared import descriptor
from PIL import Image

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def computer():
    buffer = io.BytesIO()
    Image.new("RGB", (1280, 800), "white").save(buffer, format="PNG")

    commands = SimpleNamespace(
        closed=False,
        sandbox=object(),
        configuration=E2BEnvironmentConfiguration(desktop=True),
        execution_id="exec-test",
        generation="generation-test",
    )
    computer = E2BComputerOperations(commands)
    description = DesktopDescription(width=1280, height=800, generation="desktop-test")
    computer.desktop.observe = AsyncMock(return_value=(description, buffer.getvalue()))
    computer.desktop.execute = AsyncMock()
    computer.desktop.describe = AsyncMock(return_value=description)
    return computer


def test_desktop_defaults_and_legacy_fingerprint():
    import hashlib

    ordinary = E2BEnvironmentConfiguration()
    historical = json.dumps(
        {"template": "base", "root": "/home/user", "user": "user", "allow_internet_access": True}, separators=(",", ":")
    )
    assert ordinary.fingerprint == hashlib.sha256(historical.encode()).hexdigest()
    assert E2BEnvironmentConfiguration(desktop=False, desktop_width=2048).fingerprint == ordinary.fingerprint
    assert E2BEnvironmentConfiguration(desktop=True).template == "desktop"
    custom = E2BEnvironmentConfiguration(desktop=True, template="custom")
    assert custom.template == "custom"
    assert custom.fingerprint != E2BEnvironmentConfiguration(template="custom").fingerprint
    assert "computer" not in descriptor(ordinary).operation_families
    assert "computer" in descriptor(custom).operation_families


async def test_scaled_observation_and_zero_edge_coordinates(computer):
    screenshot = await computer.observe(max_dimension=640)
    assert (screenshot.observation.width, screenshot.observation.height) == (640, 400)
    assert screenshot.data.startswith(b"\xff\xd8")
    result = await computer.execute(ComputerClick(observation=screenshot.observation, point=ComputerPoint(x=0, y=399)))
    assert result.effect == "executed"
    request, basis = computer.desktop.execute.call_args.args
    assert request.point == ComputerPoint(x=0, y=798)
    assert basis.width == 1280
    assert result.receipt.execution_id == "exec-test"


async def test_outside_foreign_and_expired_observations_never_dispatch(computer):
    screenshot = await computer.observe()
    count = computer.desktop.execute.call_count
    for observation, point in [
        (screenshot.observation, ComputerPoint(x=1280, y=0)),
        (screenshot.observation.model_copy(update={"execution_id": "exec-other"}), ComputerPoint(x=0, y=0)),
    ]:
        with pytest.raises(EnvironmentError):
            await computer.execute(ComputerClick(observation=observation, point=point))
    from dataclasses import replace

    key = screenshot.observation.observation_id
    computer._observations[key] = replace(computer._observations[key], expires=time.monotonic() - 1)
    with pytest.raises(EnvironmentError):
        await computer.execute(ComputerClick(observation=screenshot.observation, point=ComputerPoint(x=0, y=0)))
    assert computer.desktop.execute.call_count == count


async def test_invalid_scroll_text_and_keys_rejected_before_dispatch(computer):
    screenshot = await computer.observe()
    count = computer.desktop.execute.call_count
    for request in [
        ComputerScroll(observation=screenshot.observation, point=ComputerPoint(x=0, y=0), delta_y=1),
        ComputerScroll(observation=screenshot.observation, point=ComputerPoint(x=0, y=0), unit="steps", delta_x=1),
        ComputerPressKeys(keys=("enter;touch /tmp/injected",)),
        ComputerPressKeys(keys=("shift", "shift")),
        ComputerTypeText(text="中" * 6000),
    ]:
        with pytest.raises(EnvironmentError):
            await computer.execute(request)
    assert computer.desktop.execute.call_count == count


async def test_input_uncertainty_blocks_replay_but_allows_observation(computer):
    computer.desktop.execute.side_effect = TimeoutError("response lost")
    with pytest.raises(TimeoutError):
        await computer.execute(ComputerTypeText(text="hello"))
    with pytest.raises(EnvironmentError):
        await computer.execute(ComputerTypeText(text="hello"))
    assert computer.desktop.execute.call_count == 1
    assert not (await computer.describe()).input_ready


async def test_close_fences_observations(computer):
    screenshot = await computer.observe()
    await computer.close()
    computer.commands.closed = True
    with pytest.raises(EnvironmentError):
        await computer.observe()
    assert screenshot.observation.observation_id not in computer._observations


async def test_native_request_preserves_drag_button_duration_and_vertical_scroll(computer):
    screenshot = await computer.observe()
    await computer.execute(
        ComputerDrag(
            observation=screenshot.observation,
            start=ComputerPoint(x=0, y=0),
            end=ComputerPoint(x=100, y=100),
            button="right",
            duration_ms=1500,
        )
    )
    request = computer.desktop.execute.call_args.args[0]
    assert request.button == "right" and request.duration_ms == 1500
    await computer.execute(
        ComputerScroll(observation=screenshot.observation, point=ComputerPoint(x=0, y=0), unit="steps", delta_y=3)
    )
    request = computer.desktop.execute.call_args.args[0]
    assert request.delta_y == 3


@pytest.fixture
def sdk_desktop():
    from e2b import CommandResult
    from e2b.connection_config import ConnectionConfig
    from e2b_desktop import Sandbox
    from packaging.version import Version

    client = Sandbox(
        sandbox_id="sandbox-test",
        sandbox_domain=None,
        envd_version=Version("0.6.2"),
        envd_access_token="test-token",
        traffic_access_token=None,
        connection_config=ConnectionConfig(api_key="test-key"),
    )

    def command(cmd, **kwargs):
        stdout = ""
        if cmd == "xrandr":
            stdout = "Screen 0: minimum 1 x 1, current 1280x800"
        elif cmd.startswith("ps -C"):
            stdout = "10 Thu Oct 15 10:00:00 2026\n11"
        elif cmd == "xdotool getmouselocation":
            stdout = "x:100 y:100 screen:0 window:1"
        return CommandResult(stdout=stdout, stderr="", exit_code=0, error=None)

    client.commands.run = Mock(side_effect=command)
    native = client.commands.run
    configure(client, E2BEnvironmentConfiguration(desktop=True))
    desktop = Desktop(object(), E2BEnvironmentConfiguration(desktop=True))
    desktop.__dict__["client"] = client
    yield desktop, native
    client._envd_api.close()


async def test_real_sdk_click_zero_and_same_position_are_adapted(sdk_desktop):
    desktop, native = sdk_desktop
    from datetime import UTC, datetime

    from a13n_harness.environment.computer import ComputerObservation

    observation = ComputerObservation(
        execution_id="exec",
        observed_generation="gen",
        observation_id="screen",
        target_id="display",
        width=1280,
        height=800,
        mime_type="image/jpeg",
        captured_at=datetime.now(UTC),
    )
    basis = await desktop.describe()
    for point in [ComputerPoint(x=0, y=0), ComputerPoint(x=100, y=100)]:
        await desktop.execute(ComputerClick(observation=observation, point=point), basis)
    commands = [call.args[0] for call in native.call_args_list]
    assert "xdotool mousemove --sync 0 0" in commands
    assert "xdotool mousemove --sync 100 100" not in commands
    assert commands.count("xdotool click 1") == 2
    assert all(call.kwargs["user"] == "user" for call in native.call_args_list)
    assert all(call.kwargs["envs"]["LC_ALL"] == "C.UTF-8" for call in native.call_args_list)


async def test_real_sdk_literal_text_key_chords_and_failure_are_not_replayed(sdk_desktop):
    desktop, native = sdk_desktop
    from shlex import split

    from a13n_harness.environment.errors import EnvironmentProviderError

    await desktop.execute(ComputerTypeText(text="中文 'quoted' $(touch /tmp/no)"), None)
    assert "".join(split(call.args[0])[-1] for call in native.call_args_list) == "中文 'quoted' $(touch /tmp/no)"
    await desktop.execute(ComputerPressKeys(keys=("control", "a")), None)
    assert native.call_args.args[0] == "xdotool key Control_L+a"
    native.reset_mock()
    native.side_effect = TimeoutError("private error")
    with pytest.raises(EnvironmentProviderError):
        await desktop.execute(ComputerTypeText(text="hello"), None)
    assert native.call_count == 1


async def test_real_sdk_multiline_text_keeps_empty_lines_and_default_typing_pace(sdk_desktop):
    from shlex import split

    desktop, native = sdk_desktop
    text = "中文" + "abcdef" * 5
    await desktop.execute(ComputerTypeText(text="\n" + text + "\r\n\r\n'quoted'\r"), None)
    calls = [call.args[0] for call in native.call_args_list]
    assert calls[0] == calls[-1] == "xdotool key Return"
    assert calls.count("xdotool key Return") == 4
    typed = [split(command) for command in calls if command.startswith("xdotool type")]
    assert [args[-1] for args in typed] == ["中", "文", text[2:27], text[27:], "'quoted'"]
    assert all(args[:5] == ["xdotool", "type", "--delay", "75", "--"] for args in typed)


async def test_text_budget_stops_before_next_chunk_without_replaying(sdk_desktop, monkeypatch):
    from a13n_harness.environment.errors import EnvironmentProviderError
    from a13n_harness.providers.environment.e2b import desktop as module

    desktop, native = sdk_desktop
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 31])))
    with pytest.raises(EnvironmentProviderError):
        await desktop.execute(ComputerTypeText(text="中" * 50), None)
    assert native.call_count == 1


async def test_real_sdk_drag_releases_selected_button_on_failure(sdk_desktop):
    desktop, native = sdk_desktop
    from datetime import UTC, datetime

    from a13n_harness.environment.computer import ComputerObservation
    from a13n_harness.environment.errors import EnvironmentProviderError

    observation = ComputerObservation(
        execution_id="exec",
        observed_generation="gen",
        observation_id="screen",
        target_id="display",
        width=1280,
        height=800,
        mime_type="image/jpeg",
        captured_at=datetime.now(UTC),
    )
    original = native.side_effect

    def fail(cmd, **kwargs):
        if cmd.startswith("xdotool mousemove"):
            raise TimeoutError()
        return original(cmd, **kwargs)

    native.side_effect = fail
    with pytest.raises(EnvironmentProviderError):
        await desktop.execute(
            ComputerDrag(
                observation=observation,
                start=ComputerPoint(x=100, y=100),
                end=ComputerPoint(x=200, y=200),
                button="right",
                duration_ms=100,
            ),
            await desktop.describe(),
        )
    commands = [call.args[0] for call in native.call_args_list]
    assert "xdotool mousedown 3" in commands
    assert commands[-1] == "xdotool mouseup 3"


async def test_generation_change_refuses_input_without_poisoning_scope(computer):
    screen = await computer.observe()
    computer.desktop.execute.side_effect = EnvironmentError(
        "Stale", code="environment_stale_mount", details={"dispatch_stage": "pre_dispatch"}
    )
    with pytest.raises(EnvironmentError):
        await computer.execute(ComputerClick(observation=screen.observation, point=ComputerPoint(x=0, y=0)))
    assert (await computer.describe()).input_ready


async def test_cancellation_drains_sdk_thread_and_keeps_event_loop_responsive():
    import asyncio
    import threading

    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocking():
        started.set()
        release.wait(5)
        finished.set()

    pending = asyncio.create_task(run_sync(blocking))
    while not started.is_set():
        await asyncio.sleep(0)
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert finished.is_set()


async def test_sdk_stderr_zero_exit_is_not_success(sdk_desktop):
    from e2b import CommandResult

    desktop, native = sdk_desktop
    native.side_effect = None
    native.return_value = CommandResult(stdout="", stderr="Invalid multi-byte sequence", exit_code=0, error=None)
    with pytest.raises(EnvironmentError):
        await desktop.execute(ComputerTypeText(text="中文"), None)
    assert native.call_count == 1


@pytest.mark.parametrize("fail", [False, True])
async def test_official_create_owns_startup_and_failure_cleanup(monkeypatch, fail):
    from a13n_harness.environment.errors import EnvironmentProviderError
    from a13n_harness.providers.environment.e2b.desktop import create_desktop
    from e2b import CommandResult
    from e2b import Sandbox as BaseSandbox
    from e2b.connection_config import ConnectionConfig
    from e2b.sandbox_sync.commands.command import Commands
    from e2b_desktop import Sandbox
    from packaging.version import Version

    events = []

    def allocate(cls, **kwargs):
        assert kwargs["template"] == "desktop"
        assert kwargs["lifecycle"]["auto_resume"] is False
        return cls(
            sandbox_id="sdk-created",
            sandbox_domain=None,
            envd_version=Version("0.6.2"),
            envd_access_token="test-token",
            traffic_access_token=None,
            connection_config=ConnectionConfig(api_key="test"),
        )

    def run(self, cmd, **kwargs):
        assert events[0] == "sdk-created"
        events.append(cmd)
        if cmd.startswith("Xvfb") and fail:
            raise RuntimeError("Startup failed")
        if kwargs.get("background"):
            return SimpleNamespace(disconnect=lambda: None, pid=123)
        return CommandResult(stdout="ready", stderr="", exit_code=0, error=None)

    monkeypatch.setattr(BaseSandbox, "create", classmethod(allocate))
    monkeypatch.setattr(Commands, "run", run)
    monkeypatch.setattr(Sandbox, "kill", lambda self: events.append("kill"))
    operation = create_desktop(
        E2BEnvironmentConfiguration(desktop=True),
        timeout=300,
        metadata={"a13n_environment": "env-test"},
        options={},
        allocated=events.append,
    )
    if fail:
        with pytest.raises(EnvironmentProviderError):
            await operation
        assert events[-1] == "kill"
    else:
        await operation
        assert "startxfce4" in events
        assert "kill" not in events
    assert any(event.startswith("Xvfb :0") for event in events)


async def test_read_only_desktop_attachment_reuses_validated_routing(monkeypatch):
    from e2b import AsyncSandbox
    from e2b.connection_config import ConnectionConfig
    from e2b_desktop import Sandbox
    from packaging.version import Version

    connection = ConnectionConfig(
        domain="sandbox.example",
        api_url="https://api.sandbox.example",
        api_key="test",
        extra_sandbox_headers={"X-Access-Token": "test-token", "E2b-Sandbox-Id": "sandbox-test"},
    )
    base = AsyncSandbox(
        sandbox_id="sandbox-test",
        sandbox_domain="sandbox.example",
        envd_version=Version("0.6.2"),
        envd_access_token="test-token",
        traffic_access_token=None,
        connection_config=connection,
    )
    monkeypatch.setattr(Sandbox, "connect", Mock(side_effect=AssertionError("Must not resume")))
    monkeypatch.setattr(Sandbox, "create", Mock(side_effect=AssertionError("Must not allocate")))
    desktop = Desktop(base, E2BEnvironmentConfiguration(desktop=True))
    try:
        assert desktop.client.sandbox_id == base.sandbox_id
        assert desktop.client.connection_config is connection
        assert desktop.client._envd_access_token == base._envd_access_token
    finally:
        await desktop.close()
        await base._envd_api.aclose()


async def test_cancelled_input_holds_scope_lock_until_sdk_finishes(computer):
    import asyncio
    import threading

    started, release = threading.Event(), threading.Event()

    def input_call():
        started.set()
        release.wait(5)

    computer.desktop.execute = lambda *_: run_sync(input_call)
    first = asyncio.create_task(computer.execute(ComputerTypeText(text="once")))
    while not started.is_set():
        await asyncio.sleep(0)
    first.cancel()
    second = asyncio.create_task(computer.execute(ComputerTypeText(text="again")))
    closing = asyncio.create_task(computer.close())
    await asyncio.sleep(0)
    assert not second.done() and not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(EnvironmentError):
        await second
    await closing
