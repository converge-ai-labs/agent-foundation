"""Opt-in native Windows desktop tests against an explicitly provisioned disposable VM.

The daemon must run as the ordinary interactive user. These tests never elevate,
change desktop security, or use a hypervisor input path.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from a13n_envd_client import EIPDeviceConnection, HttpTransport
from a13n_envd_client.eip import v1 as p
from a13n_envd_client.errors import EIPMethodError
from a13n_environment.models import COMPUTER_ACTIONS, EnvironmentPermissionSet
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_device_runtime import HEADERS, app_listener, resource

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_WINDOWS_TEST") != "1", reason="Requires a disposable Windows desktop"),
]


def context():
    return p.EIPCallContext(operation_id="native-" + uuid.uuid4().hex)


def settings():
    return (
        os.environ["A13N_WINDOWS_ENDPOINT"],
        Path(os.environ["A13N_WINDOWS_CREDENTIAL_FILE"]).read_text().strip(),
        os.environ["A13N_WINDOWS_DEVICE_ID"],
        os.environ["A13N_WINDOWS_DIRECTORY"].rstrip("/"),
    )


async def read_state(session, directory):
    async with session.open_reader(p.EIPPath(path=directory + "/desktop.json")) as reader:
        return json.loads(b"".join([chunk async for chunk in reader]))


async def state_until(session, directory, predicate):
    state = None
    try:
        async with asyncio.timeout(20):
            while True:
                try:
                    state = await read_state(session, directory)
                except (EIPMethodError, json.JSONDecodeError):
                    state = None
                if state is not None and predicate(state):
                    return state
                await asyncio.sleep(0.05)
    except TimeoutError:
        pytest.fail(f"Desktop state did not converge: {state!r}")


@asynccontextmanager
async def desktop(keyboard_layout="00000409"):
    endpoint, credential, device_id, directory = settings()
    device = await EIPDeviceConnection.initialize(
        HttpTransport(endpoint=endpoint, credential=credential), expected_device_id=device_id
    )
    async with device, await device.open_session() as session:
        source = Path(__file__).resolve().parents[3] / "dev/fixtures/windows-desktop/desktop.ps1"
        async with session.open_writer(p.EIPPath(path=directory + "/desktop.ps1"), mode="upsert") as writer:
            await writer.write(source.read_bytes())
            await writer.commit()
        native = directory.removeprefix("/").replace("/", "\\")
        # Reset stale evidence before launching the new Session-owned fixture.
        await session.client.shell_exec(
            p.ShellExecParams(
                context=context(),
                request=p.CommandRequest(
                    command=p.ShellCommand(
                        kind="shell",
                        profile_id="default",
                        script=f"Remove-Item -ErrorAction SilentlyContinue '{native}\\desktop.json'",
                    )
                ),
            )
        )
        await session.client.process_start(
            p.ProcessStartParams(
                context=context(),
                request=p.CommandRequest(
                    command=p.ShellCommand(
                        kind="shell",
                        profile_id="default",
                        script=f"& ([scriptblock]::Create((Get-Content -Raw '{native}\\desktop.ps1'))) -StatePath '{native}\\desktop.json' -KeyboardLayout '{keyboard_layout}'",
                    )
                ),
            )
        )
        await state_until(session, directory, lambda state: state["ready"] and state["clicks"] == 0)
        yield session, directory


async def observe(session, max_dimension=1280):
    async with session.observe_computer(max_dimension=max_dimension) as reader:
        observation = reader.opened.observation
        data = b"".join([chunk async for chunk in reader])
    return observation.observation_id, data


def executed(result):
    assert result.effect is p.ComputerEffect.EXECUTED, result
    assert result.input_cleanup_complete


async def test_native_windows_gestures_evidence_and_cancellation(tmp_path):
    async with desktop() as (session, directory):
        client = session.client
        described = await client.computer_describe(p.ComputerDescribeParams(context=context()))
        assert described.observe_ready and described.input_ready
        assert described.scroll_units == (p.ComputerScrollUnit.STEPS,)
        reference, before = await observe(session)
        image = Image.open(BytesIO(before))
        assert image.getpixel((70, 100))[0] > 200
        click = p.ComputerClickParams(
            context=context(),
            observation_id=reference,
            point=p.ComputerPoint(x=100, y=100),
            button=p.ComputerButton.LEFT,
        )
        executed(await client.computer_click(click))
        executed(await client.computer_click(click))  # Same operation evidence, not a second dispatch.
        await state_until(session, directory, lambda state: state["clicks"] == 1)
        text = "Hello Windows 中文 \U0001f600\r\nsecond\tcolumn"
        executed(await client.computer_type_text(p.ComputerTypeTextParams(context=context(), text=text)))
        state = await state_until(session, directory, lambda state: "column" in state["text"])
        assert state["text"] == text
        executed(await client.computer_press_keys(p.ComputerPressKeysParams(context=context(), keys=("control", "a"))))
        await state_until(session, directory, lambda state: "A, Control" in state["keys"] and not state["held"])
        # The .NET Framework multiline edit does not implement Ctrl+A. Select by navigation.
        for keys in (("control", "home"), ("control", "shift", "end")):
            executed(await client.computer_press_keys(p.ComputerPressKeysParams(context=context(), keys=keys)))
        executed(await client.computer_type_text(p.ComputerTypeTextParams(context=context(), text="replacement")))
        await state_until(session, directory, lambda state: state["text"] == "replacement")
        for button in (p.ComputerButton.RIGHT, p.ComputerButton.MIDDLE):
            executed(await client.computer_click(click.model_copy(update={"context": context(), "button": button})))
        for args in (
            p.ComputerScrollParams(
                context=context(),
                observation_id=reference,
                point=p.ComputerPoint(x=900, y=500),
                delta_x=2,
                delta_y=3,
                unit=p.ComputerScrollUnit.STEPS,
            ),
        ):
            executed(await client.computer_scroll(args))
        state = await state_until(session, directory, lambda state: state["vertical_wheel"] != 0)
        assert (state["vertical_wheel"], state["horizontal_wheel"]) == (-360, 240)
        assert state["buttons"][:3] == ["Left", "Right", "Middle"]
        drag = p.ComputerDragParams(
            context=context(),
            observation_id=reference,
            start=p.ComputerPoint(x=800, y=400),
            end=p.ComputerPoint(x=900, y=600),
            button=p.ComputerButton.LEFT,
            duration_ms=300,
        )
        executed(await client.computer_drag(drag))
        await state_until(
            session, directory, lambda state: state["drag"] and state["drag"][-1] == [900, 600] and not state["held"]
        )
        cancelled = drag.model_copy(update={"context": context(), "duration_ms": 3000})
        pending = asyncio.create_task(client.computer_drag(cancelled))
        await state_until(session, directory, lambda state: state["dragging"] and 1 in state["held"])
        cancel = await client.operation_cancel(
            p.OperationCancelParams(context=context(), target_operation_id=cancelled.context.operation_id)
        )
        assert cancel.status is p.OperationCancelStatus.CANCELLATION_REQUESTED
        result = await pending
        assert result.effect is p.ComputerEffect.PARTIAL and result.input_cleanup_complete
        await state_until(session, directory, lambda state: not state["dragging"] and not state["held"])
        with pytest.raises(EIPMethodError):
            await client.computer_click(
                click.model_copy(update={"context": context(), "point": p.ComputerPoint(x=9999, y=0)})
            )
        with pytest.raises(EIPMethodError):
            await client.computer_scroll(
                args.model_copy(update={"context": context(), "unit": p.ComputerScrollUnit.PIXELS})
            )
        _, after = await observe(session)
        assert Image.open(BytesIO(after)).getpixel((70, 100))[1] > 200
        (tmp_path / "before.jpg").write_bytes(before)
        (tmp_path / "after.jpg").write_bytes(after)
        (tmp_path / "state.json").write_text(json.dumps(await read_state(session, directory), ensure_ascii=False))
        # A new Session cannot use the old Session's geometry, even on the same Device.
        endpoint, credential, device_id, _ = settings()
        other = await EIPDeviceConnection.initialize(
            HttpTransport(endpoint=endpoint, credential=credential), expected_device_id=device_id
        )
        async with other, await other.open_session() as new_session:
            with pytest.raises(EIPMethodError):
                await new_session.client.computer_click(click.model_copy(update={"context": context()}))
            await observe(new_session)


async def test_native_windows_reaches_model_and_saved_webui_image(tmp_path, monkeypatch):
    endpoint, credential, device_id, directory = settings()
    monkeypatch.setenv("DEVICE_TOKEN", credential)
    recipe = resource("http", endpoint).model_copy(update={"device_id": device_id})
    steps = 0
    reference = None
    images = []
    calls = [
        ("computer_describe", {"alias": "desktop"}),
        ("computer_observe", {"alias": "desktop", "max_dimension": 1280}),
        ("computer_click", {"point": {"x": 100, "y": 100}}),
        ("computer_type_text", {"alias": "desktop", "text": "Harness Windows 中文 \U0001f600"}),
        ("computer_observe", {"alias": "desktop", "max_dimension": 1280}),
    ]

    async def model(messages, info):
        nonlocal steps, reference
        assert "computer_type_text" in {tool.name for tool in info.function_tools}
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
                red, green, _ = Image.open(BytesIO(images[-1])).getpixel((70, 100))
                assert red > green if len(images) == 1 else green > red
            elif steps > 2:
                assert result["effect"] == "executed" and result["input_cleanup_complete"]
        if steps == len(calls):
            yield "Verified native Windows screenshot, click and Unicode text."
            return
        name, args = calls[steps]
        if name == "computer_click":
            args = {**args, "observation_id": reference}
        steps += 1
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"windows-{steps}")}

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with desktop() as (session, _), app_listener(tmp_path, recipe) as (app, http, _ws):
        agent = tmp_path / "agents/assistant.yaml"
        await app.mutate_configuration(
            relative_path="agents/assistant.yaml",
            request=ResourceMutationRequest(
                content=agent.read_text() + "capabilities:\n  - capability: dynamic_environment\n"
            ),
        )
        thread = await app.create_thread(
            defaults=NewThreadDefaults(
                project_id=None,
                environment_bindings=(
                    EnvironmentBindingSelection(
                        device_id="device-build",
                        alias="desktop",
                        working_directory=directory,
                        permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS),
                    ),
                ),
                default_environment="desktop",
            )
        )
        async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Verify the Windows desktop.")
            live_images = []
            async with asyncio.timeout(60):
                while len(live_images) < 2:
                    event = await watch.events.receive()
                    if event.payload and event.payload.get("name") == "a13n.harness-ui.tool_images":
                        live_images.append(event.payload["value"]["event"])
                operation = await app.wait_root_operation(receipt.receipt_id)
            assert operation.status is RootOperationStatus.completed, operation
        state = await state_until(session, directory, lambda state: state["text"] == "Harness Windows 中文 \U0001f600")
        assert state["clicks"] == 1 and not state["held"]
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


async def test_native_windows_swapped_buttons_and_scaled_observation():
    async with desktop() as (session, directory):

        async def swap(enabled):
            script = (
                "Add-Type -TypeDefinition 'using System.Runtime.InteropServices; "
                'public class MouseSetup { [DllImport("user32.dll")] '
                "public static extern bool SwapMouseButton(bool swap); }'; "
                f"[MouseSetup]::SwapMouseButton(${str(enabled).lower()}) | Out-Null"
            )
            result = await session.client.shell_exec(
                p.ShellExecParams(
                    context=context(),
                    request=p.CommandRequest(command=p.ShellCommand(kind="shell", profile_id="default", script=script)),
                )
            )
            assert result.status.exit_code == 0

        try:
            await swap(True)
            reference, image = await observe(session, max_dimension=640)
            assert Image.open(BytesIO(image)).size == (640, 400)
            for button in (p.ComputerButton.LEFT, p.ComputerButton.RIGHT):
                executed(
                    await session.client.computer_click(
                        p.ComputerClickParams(
                            context=context(),
                            observation_id=reference,
                            point=p.ComputerPoint(x=50, y=50),
                            button=button,
                        )
                    )
                )
            state = await state_until(session, directory, lambda state: state["clicks"] == 2)
            assert state["buttons"] == ["Left", "Right"]
            assert not state["held"]
        finally:
            await swap(False)


async def test_native_windows_foreground_layout_and_external_held_key():
    # Establish US input first: running only the French case can accidentally
    # give the daemon worker the same layout and miss the held-key regression.
    async with desktop() as (session, _):
        executed(
            await session.client.computer_press_keys(p.ComputerPressKeysParams(context=context(), keys=("escape",)))
        )
    async with desktop(keyboard_layout="0000040C") as (session, directory):
        state = await state_until(session, directory, lambda state: state["keyboard_layout"] & 0xFFFF == 0x040C)
        assert not state["held"]
        source = Path(__file__).resolve().parents[3] / "dev/fixtures/windows-desktop/external-key.ps1"
        async with session.open_writer(p.EIPPath(path=directory + "/external-key.ps1"), mode="upsert") as writer:
            await writer.write(source.read_bytes())
            await writer.commit()
        native = directory.removeprefix("/").replace("/", "\\")

        async def external_key(release=False, scan=30):
            result = await session.client.shell_exec(
                p.ShellExecParams(
                    context=context(),
                    request=p.CommandRequest(
                        command=p.ShellCommand(
                            kind="shell",
                            profile_id="default",
                            script=f"& ([scriptblock]::Create((Get-Content -Raw '{native}\\external-key.ps1'))) -ScanCode {scan} {'-Release' if release else ''}",
                        )
                    ),
                )
            )
            assert result.status.exit_code == 0

        try:
            await external_key()
            await state_until(session, directory, lambda state: 81 in state["held"] and 65 not in state["held"])
            with pytest.raises(EIPMethodError) as failure:
                await session.client.computer_press_keys(p.ComputerPressKeysParams(context=context(), keys=("a",)))
            assert failure.value.error.data.safe_detail == "computer_input_held"
            assert 81 in (await read_state(session, directory))["held"]
        finally:
            await external_key(release=True)
        await state_until(session, directory, lambda state: not state["held"])
        executed(await session.client.computer_press_keys(p.ComputerPressKeysParams(context=context(), keys=("a",))))
        await state_until(session, directory, lambda state: "Q" in state["keys"] and not state["held"])
        try:
            await external_key(scan=42)  # Independently held Shift must block Unicode entry.
            await state_until(session, directory, lambda state: 16 in state["held"])
            with pytest.raises(EIPMethodError) as failure:
                await session.client.computer_type_text(p.ComputerTypeTextParams(context=context(), text="blocked"))
            assert failure.value.error.data.safe_detail == "computer_input_held"
            assert failure.value.error.data.dispatch_stage is p.DispatchStage.PRE_DISPATCH
            assert 16 in (await read_state(session, directory))["held"]
        finally:
            await external_key(release=True, scan=42)
        await state_until(session, directory, lambda state: not state["held"])


@pytest.mark.skipif(
    os.environ.get("A13N_WINDOWS_UNAVAILABLE") != "1",
    reason="Requires the operator to lock the disposable desktop or open its UAC secure desktop",
)
async def test_native_windows_desktop_unavailable_rejects_capture_and_input():
    endpoint, credential, device_id, _ = settings()
    device = await EIPDeviceConnection.initialize(
        HttpTransport(endpoint=endpoint, credential=credential), expected_device_id=device_id
    )
    async with device, await device.open_session() as session:
        with pytest.raises(EIPMethodError) as failure:
            await session.client.computer_describe(p.ComputerDescribeParams(context=context()))
        assert failure.value.error.data.safe_detail == "computer_windows_desktop_unavailable"
        with pytest.raises(EIPMethodError) as failure:
            async with session.observe_computer():
                pytest.fail("Unavailable desktop must not publish a screenshot")
        assert failure.value.error.data.safe_detail == "computer_windows_desktop_unavailable"
        for method, params in (
            (session.client.computer_type_text, p.ComputerTypeTextParams(context=context(), text="must not be typed")),
            (session.client.computer_press_keys, p.ComputerPressKeysParams(context=context(), keys=("escape",))),
        ):
            with pytest.raises(EIPMethodError) as failure:
                await method(params)
            error = failure.value.error
            assert error.data.safe_detail == "computer_windows_desktop_unavailable"
            assert error.data.dispatch_stage is p.DispatchStage.PRE_DISPATCH
