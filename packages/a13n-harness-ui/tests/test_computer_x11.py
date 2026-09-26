"""Opt-in real Xvfb / Tk / envd / reverse WebSocket / App integration.

Run only in the disposable image documented in dev/fixtures/linux-desktop.
No host desktop socket, credentials, services or configuration are used.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from a13n_harness.providers.environment.models import COMPUTER_ACTIONS, EnvironmentPermissionSet
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_device_runtime import HEADERS, TOKEN, app_listener, resource

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_X11_TEST") != "1", reason="Requires the disposable X11 test image"),
]


@asynccontextmanager
async def process(*args, env=None):
    child = await asyncio.create_subprocess_exec(
        *args, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        yield child
    finally:
        if child.returncode is None:
            child.terminate()
        try:
            _, stderr = await asyncio.wait_for(child.communicate(), 5)
        except TimeoutError:
            child.kill()
            await child.communicate()
            raise
        assert TOKEN.encode() not in stderr
        assert child.returncode in (0, -15), stderr.decode(errors="replace")


async def wait_until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(0.02)


@asynccontextmanager
async def desktop(tmp_path):
    state = tmp_path / "desktop.json"
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(process("Xvfb", ":99", "-screen", "0", "1024x768x24", "-nolisten", "tcp"))
        await wait_until(lambda: Path("/tmp/.X11-unix/X99").exists())
        await stack.enter_async_context(process("openbox"))
        fixture = Path(__file__).resolve().parents[3] / "dev/fixtures/linux-desktop/desktop.py"
        await stack.enter_async_context(process("/usr/bin/python3", str(fixture), str(state)))
        await wait_until(lambda: state.exists() and json.loads(state.read_text())["ready"])
        # Keep persistent clients connected before changing the server mapping.
        # XTEST consumes physical button numbers; the API promises logical buttons.
        mapping = await asyncio.create_subprocess_exec("xmodmap", "-e", "pointer = 3 2 1 5 4 7 6")
        assert await mapping.wait() == 0
        yield state


@asynccontextmanager
async def daemon(tmp_path, ws):
    credential = tmp_path / "credential"
    credential.write_text(TOKEN)
    config = tmp_path / "envd.json"
    config.write_text(
        json.dumps({"device_id": "native-build", "computer_use": True, "default_working_directory": "/tmp"})
    )
    environment = {
        "DISPLAY": ":99",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "A13N_ENVD_RUNTIME_DIR": str(tmp_path / "runtime"),
        "A13N_ENVD_STATE_DIR": str(tmp_path / "installation"),
        "A13N_ENVD_TRANSPORT": "reverse_websocket",
        "A13N_ENVD_REVERSE_WS_URL": ws + "/api/devices/device-build/connect",
        "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential),
    }
    async with process(os.environ["A13N_ENVD_TEST_BINARY"], "--config", str(config), env=environment):
        yield


async def test_real_x11_desktop_reaches_model_and_saved_webui_image(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    steps = 0
    reference = None
    images = []
    calls = [
        ("computer_describe", {"alias": "desktop"}),
        ("computer_observe", {"alias": "desktop", "max_dimension": 1024}),
        ("computer_click", {"point": {"x": 100, "y": 120}}),
        ("computer_press_keys", {"alias": "desktop", "keys": ["a"]}),
        ("computer_scroll", {"point": {"x": 400, "y": 300}, "delta_y": 3, "delta_x": 2, "unit": "steps"}),
        ("computer_drag", {"start": {"x": 400, "y": 400}, "end": {"x": 600, "y": 500}, "duration_ms": 200}),
        ("computer_observe", {"alias": "desktop", "max_dimension": 1024}),
    ]

    async def model(messages, info):
        nonlocal steps, reference
        assert "computer_type_text" not in {tool.name for tool in info.function_tools}
        returns = [
            part
            for message in messages
            if message.kind == "request"
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            result = returns[-1].content
            assert result["ok"], result
            if steps == 1:
                assert result["scroll_units"] == ["steps"]
                assert result["targets"][0]["width"] == 1024
            if "observation_id" in result:
                reference = result["observation_id"]
                binaries = [
                    part
                    for message in messages
                    if message.kind == "request"
                    for prompt in message.parts
                    if isinstance(prompt, UserPromptPart) and not isinstance(prompt.content, str)
                    for part in prompt.content
                    if isinstance(part, BinaryContent)
                ]
                images.append(binaries[-1].data)
                decoded = Image.open(BytesIO(images[-1]))
                assert decoded.size == (1024, 768)
                red, green, _blue = decoded.getpixel((70, 100))
                assert (red > green) if len(images) == 1 else (green > red)
            elif steps > 2:
                assert result["effect"] == "executed" and result["input_cleanup_complete"], result
        if steps == len(calls):
            yield "Verified real X11 screenshot, click, physical key, wheel steps and drag."
            return
        name, args = calls[steps]
        if name in ("computer_click", "computer_scroll", "computer_drag"):
            args = {**args, "observation_id": reference}
        steps += 1
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"x11-{steps}")}

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with desktop(tmp_path) as state, app_listener(tmp_path, resource("websocket", "")) as (app, http, ws):
        agent = tmp_path / "agents/assistant.yaml"
        await app.mutate_configuration(
            relative_path="agents/assistant.yaml",
            request=ResourceMutationRequest(
                content=agent.read_text() + "capabilities:\n  - capability: dynamic_environment\n"
            ),
        )
        async with daemon(tmp_path, ws):
            async with asyncio.timeout(15):
                while not (await app.device_info("device-build")).available:
                    await asyncio.sleep(0.02)
            thread = await app.create_thread(
                defaults=NewThreadDefaults(
                    project_id=None,
                    environment_bindings=(
                        EnvironmentBindingSelection(
                            device_id="device-build",
                            alias="desktop",
                            working_directory="/tmp",
                            permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS),
                        ),
                    ),
                    default_environment="desktop",
                )
            )
            async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
                receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Verify the X11 desktop.")
                live_images = []
                async with asyncio.timeout(60):
                    while len(live_images) < 2:
                        event = await watch.events.receive()
                        if event.payload and event.payload.get("name") == "a13n.harness-ui.tool_images":
                            live_images.append(event.payload["value"]["event"])
                    operation = await app.wait_root_operation(receipt.receipt_id)
                assert operation.status is RootOperationStatus.completed, operation
            await wait_until(lambda: len(json.loads(state.read_text())["drag"]) > 0)
            recorded = json.loads(state.read_text())
            assert recorded["clicks"] == 1
            assert recorded["keys"] == ["a"]
            assert recorded["buttons"] == [[1, 0], *([[5, 0]] * 3), *([[5, 1]] * 2), [1, 0]]
            assert recorded["drag"][-1] == [600, 500]
            detail = await app.get_thread(thread.thread_id)
            transcript = await app.get_thread_transcript(
                thread_id=thread.thread_id, expected_continuation_id=detail.continuation_id
            )
            serialized = json.dumps(transcript.model_dump(mode="json"))
            async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
                for event, image in zip(live_images, images, strict=True):
                    attachment = event["images"][0]
                    identifier = attachment["attachment"]["attachment_id"]
                    assert identifier in serialized
                    url = f"/api/threads/{attachment['thread_id']}/attachments/{identifier}"
                    response = await api.get(url)
                    assert response.status_code == 200 and response.content == image
            assert images[0] != images[1]
