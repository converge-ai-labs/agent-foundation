"""Authenticated browser voice control; media flows directly over WebRTC."""

from collections.abc import Callable
from typing import Literal

from anyio import create_task_group, fail_after, sleep
from fastapi import FastAPI, WebSocket
from pydantic import Field
from starlette.websockets import WebSocketDisconnect

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.interactive_transport import InteractiveOutput, authenticate_interactive, receive_text
from a13n_harness_ui.surfaces import SurfaceModel
from a13n_harness_ui.voice import VoiceController, VoiceSnapshot


class VoiceOwner(SurfaceModel):
    owner_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class VoiceStart(VoiceOwner):
    start_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    offer_sdp: str = Field(min_length=3, max_length=128 * 1024, repr=False)


class VoiceStop(VoiceOwner):
    start_id: str = Field(min_length=1, max_length=128)


class VoiceCommand(SurfaceModel):
    kind: Literal["media_connected", "stop", "pong"]
    start_id: str = Field(default="", max_length=128)


class VoicePing(SurfaceModel):
    kind: Literal["ping"] = "ping"


def register_voice_routes(server: FastAPI, controller: Callable[[], VoiceController], api_key: str | None) -> None:
    @server.get("/api/voice")
    async def status() -> VoiceSnapshot:
        return await controller().status()

    @server.post("/api/voice/start", status_code=202)
    async def start(body: VoiceStart) -> VoiceSnapshot:
        return await controller().begin(owner_id=body.owner_id, start_id=body.start_id, offer_sdp=body.offer_sdp)

    @server.post("/api/voice/stop")
    async def stop(body: VoiceStop) -> VoiceSnapshot:
        voice = controller()
        await voice.stop(owner_id=body.owner_id, start_id=body.start_id)
        return await voice.status()

    @server.websocket("/api/voice/connect")
    async def connect(socket: WebSocket) -> None:
        if not await authenticate_interactive(socket, api_key):
            return
        output = InteractiveOutput(socket)
        try:
            with fail_after(10):
                owner = VoiceOwner.model_validate_json(await receive_text(socket, limit=512))
            voice = controller()
            async with voice.subscribe(owner.owner_id) as queue:
                async with create_task_group() as group:

                    async def publish() -> None:
                        while True:
                            await output.send(await queue.get())

                    async def heartbeat() -> None:
                        while True:
                            await sleep(20)
                            await output.send(VoicePing())

                    group.start_soon(publish)
                    group.start_soon(heartbeat)
                    try:
                        while True:
                            with fail_after(60):
                                command = VoiceCommand.model_validate_json(await receive_text(socket, limit=512))
                            if command.kind == "media_connected":
                                await voice.media_connected(owner_id=owner.owner_id, start_id=command.start_id)
                            elif command.kind == "stop":
                                await voice.stop(owner_id=owner.owner_id, start_id=command.start_id)
                    finally:
                        group.cancel_scope.cancel()
        except* HarnessUiError as errors:
            await output.close(code=4409, reason=str(errors.exceptions[0])[:120])
        except* ValueError:
            await output.close(code=4400, reason="Invalid voice control frame")
        except* (WebSocketDisconnect, TimeoutError):
            pass
