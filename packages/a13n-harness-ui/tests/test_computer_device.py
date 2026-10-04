"""Real reverse WebSocket / App / tools / browser API; only desktop and model are scripted.

This exercises portable integration, not native macOS capture or input permission.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from io import BytesIO

import httpx
import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_harness.providers.environment.models import COMPUTER_ACTIONS, EnvironmentPermissionSet
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from websockets.asyncio.client import connect

from .test_device_runtime import HEADERS, TOKEN, app_listener, resource

pytestmark = pytest.mark.anyio


def descriptors():
    common = {
        "device_id": "native-build",
        "generation": 1,
        "boundary": {
            "sandbox": {"mode": "disabled"},
            "egress": "inherit",
            "privilege_gain_blocked": False,
            "backend": "native",
            "policy_digest": "a" * 64,
        },
        "lifecycle": {"idle_timeout_ms": 30000, "disconnect_grace_ms": 1000},
        "limits": {
            "max_request_bytes": 1048576,
            "max_response_bytes": 1048576,
            "max_concurrent_operations": 8,
            "max_processes": 1,
            "max_operation_duration_ms": 30000,
            "max_output_preview_bytes": 1024,
            "max_output_bytes_per_stream": 1024,
            "max_transfer_frame_bytes": 65536,
            "max_concurrent_file_transfers": 2,
            "max_file_transfer_bytes": 4194304,
            "max_file_bytes": 4194304,
        },
    }
    device = eip.DeviceDescriptor.model_validate(
        {**common, "path_style": "posix", "default_working_directory": "/", "directory_discovery": False}
    )
    session = eip.SessionDescriptor.model_validate(
        {
            **common,
            "session_id": "ses-desktop",
            "working_directory": "/",
            "available_methods": [
                "environment.describe",
                "environment.readiness",
                "session.close",
                "session.keepalive",
                "computer.describe",
                "computer.observe",
                "computer.close_observation",
                "computer.click",
            ],
            "execution_features": {
                "process_count_limit": False,
                "memory_bytes_limit": False,
                "cpu_time_limit": False,
                "signal_interrupt": False,
                "signal_terminate": False,
            },
        }
    )
    return device.model_dump(mode="json"), session.model_dump(mode="json")


@asynccontextmanager
async def desktop_peer(ws, image):
    device, session = descriptors()
    calls = []
    async with connect(
        ws + "/api/devices/device-build/connect",
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
        subprotocols=["eip.v1"],
        proxy=None,
    ) as connection:

        async def serve():
            async for message in connection:
                if isinstance(message, bytes):
                    frame = eip.decode_data_frame(message, max_frame_bytes=65536)
                    if frame.kind is eip.DataFrameKind.ATTACH:
                        for response in (
                            eip.DataFrame(
                                session_id=frame.session_id, handle=frame.handle, kind=eip.DataFrameKind.ATTACHED
                            ),
                            eip.DataFrame(
                                session_id=frame.session_id,
                                handle=frame.handle,
                                kind=eip.DataFrameKind.CHUNK,
                                payload=image,
                            ),
                            eip.DataFrame(
                                session_id=frame.session_id,
                                handle=frame.handle,
                                kind=eip.DataFrameKind.END,
                                offset=len(image),
                            ),
                        ):
                            await connection.send(eip.encode_data_frame(response, max_frame_bytes=65536))
                    else:
                        assert frame.kind is eip.DataFrameKind.CREDIT
                    continue
                request = json.loads(message)
                method, params = request["method"], request["params"]
                calls.append((method, params))
                match method:
                    case "initialize":
                        result = {
                            "protocol_version": eip.EIP_PROTOCOL_VERSION,
                            "server": {"name": "a13n-envd", "version": "scripted-test"},
                            "descriptor": device,
                        }
                    case "device.describe":
                        result = {"descriptor": device}
                    case "session.open" | "environment.describe":
                        result = {"descriptor": session}
                    case "environment.readiness":
                        result = {
                            "ready": True,
                            "device_id": "native-build",
                            "generation": 1,
                            "session_id": "ses-desktop",
                        }
                    case "session.keepalive":
                        result = {"alive": True}
                    case "session.close":
                        result = {"closed": True}
                    case "computer.observe":
                        result = {
                            "observation": {
                                "observation_id": "obs-native",
                                "target_id": "display-1",
                                "width": 320,
                                "height": 200,
                                "mime_type": "image/png",
                                "captured_at": "2026-09-01T00:00:00Z",
                            },
                            "reader": "screen-one",
                            "size_bytes": len(image),
                            "expires_at": "2099-01-01T00:00:00Z",
                        }
                    case "computer.close_observation":
                        result = {
                            "completion": {
                                "produced_bytes": len(image),
                                "digest": {"algorithm": "sha256", "value": hashlib.sha256(image).hexdigest()},
                            }
                        }
                    case "computer.click":
                        assert params["observation_id"] == "obs-native"
                        assert params["point"] == {"x": 42, "y": 24}
                        result = {
                            "effect": "executed",
                            "input_cleanup_complete": True,
                            "receipt": {
                                "operation_id": params["context"]["operation_id"],
                                "method": method,
                                "device_id": "native-build",
                                "session_id": "ses-desktop",
                                "generation": 1,
                                "request_digest": "0" * 64,
                                "stage": "completed",
                                "outcome": "succeeded",
                                "observed_at": "2026-09-01T00:00:00Z",
                            },
                        }
                    case _:
                        raise AssertionError(f"Unexpected method {method}")
                response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
                if "eip_session" in request:
                    response["eip_session"] = request["eip_session"]
                await connection.send(json.dumps(response))

        task = asyncio.create_task(serve())
        try:
            yield calls
        finally:
            await connection.close()
            await task


async def test_reverse_desktop_image_reaches_model_live_history_and_browser(tmp_path, monkeypatch):
    from a13n_harness.tools._output import tool_execution_value

    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    buffer = BytesIO()
    Image.new("RGB", (320, 200), "navy").save(buffer, format="PNG")
    image = buffer.getvalue()
    steps = 0

    async def model(messages, info):
        nonlocal steps
        returns = [
            part
            for message in messages
            if message.kind == "request"
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if steps == 0:
            assert {"computer_observe", "computer_click"} <= {tool.name for tool in info.function_tools}
            name, args = "computer_observe", {"alias": "desktop"}
        elif steps == 1:
            observation = tool_execution_value(returns[-1].content, returns[-1].metadata)
            assert observation["ok"], observation
            binaries = [
                item
                for part in returns
                if isinstance(part.content, list)
                for item in part.content
                if isinstance(item, BinaryContent)
            ]
            assert any(part.data == image for part in binaries)
            name, args = (
                "computer_click",
                {"observation_id": observation["observation_id"], "point": {"x": 42, "y": 24}},
            )
        else:
            assert returns[-1].content["effect"] == "executed", returns[-1].content
            yield "Observed the remote desktop and clicked once."
            return
        steps += 1
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"desktop-{steps}")}

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with app_listener(tmp_path, resource("websocket", "")) as (app, http, ws):
        agent = tmp_path / "agents/assistant.yaml"
        await app.mutate_configuration(
            relative_path="agents/assistant.yaml",
            request=ResourceMutationRequest(
                content=agent.read_text() + "capabilities:\n  - capability: dynamic_environment\n"
            ),
        )
        async with desktop_peer(ws, image) as calls:
            async with asyncio.timeout(10):
                while not (await app.device_info("device-build")).available:
                    await asyncio.sleep(0.01)
            thread = await app.create_thread(
                defaults=NewThreadDefaults(
                    project_id=None,
                    environment_bindings=(
                        EnvironmentBindingSelection(
                            device_id="device-build",
                            alias="desktop",
                            working_directory="/",
                            permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS),
                        ),
                    ),
                    default_environment="desktop",
                )
            )
            async with app.watch_thread(root_thread_id=thread.thread_id) as watch:
                receipt = await app.submit_thread(
                    thread_id=thread.thread_id, prompt="Observe the desktop and click at 42,24."
                )
                async with asyncio.timeout(30):
                    live_image = None
                    while live_image is None:
                        event = await watch.events.receive()
                        if event.payload and event.payload.get("name") == "a13n.display.changes":
                            for change in event.payload["value"]["changes"]:
                                if change["type"] == "set" and change["item"]["content"].get("tool_images"):
                                    content = change["item"]["content"]
                                    live_image = {
                                        "tool_call_id": content["toolCallId"],
                                        "images": content["tool_images"],
                                        "unavailable": content.get("tool_image_unavailable", False),
                                    }
                    operation = await app.wait_root_operation(receipt.receipt_id)
                assert operation.status is RootOperationStatus.completed, operation
            assert live_image["tool_call_id"] == "desktop-1" and not live_image["unavailable"]
            detail = await app.get_thread(thread.thread_id)
            transcript = await app.get_thread_transcript(
                thread_id=thread.thread_id, expected_continuation_id=detail.continuation_id
            )
            serialized = transcript.model_dump(mode="json")
            assert live_image["images"][0]["attachment"]["attachment_id"] in json.dumps(serialized)
            assert "base64" not in json.dumps(serialized)
            attachment = live_image["images"][0]
            async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
                response = await api.get(
                    f"/api/threads/{attachment['thread_id']}/attachments/{attachment['attachment']['attachment_id']}"
                )
                assert response.status_code == 200 and response.content == image
            assert [method for method, _ in calls].count("computer.click") == 1
            assert [method for method, _ in calls].count("computer.close_observation") == 1
            assert [method for method, _ in calls].count("session.close") == 1
