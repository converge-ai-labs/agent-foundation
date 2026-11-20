"""Browser transport for an all-role Service realtime preview."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import ObjectId
from a13n_service.providers.realtime import PROTOCOLS
from a13n_service.resources.models.service import resolve_model
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schemas import Message, MessagePayload, NewThread, RunOptions, TextPart
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import execution_authority
from a13n_service.tenancy.requests import Actor, WorkspaceId

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

LABEL = "realtime-session"
type Frame = bytes | dict[str, str | int | bool]


@dataclass(slots=True)
class Channel:
    principal_id: str
    workspace_id: str
    incoming: asyncio.Queue[bytes | str] = field(default_factory=lambda: asyncio.Queue(64))
    outgoing: asyncio.Queue[Frame] = field(default_factory=lambda: asyncio.Queue(128))
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    attached: bool = False
    disconnected: bool = False


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: ObjectId
    thread_id: ObjectId | None = None
    agent_revision_id: ObjectId | None = None
    audio: bool = True


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "stop"]
    text: str = Field(default="", max_length=8192)


class RealtimeAvailability(BaseModel):
    realtime: bool = False
    can_start: bool = False
    accepts_text: bool = False
    reason: str | None = None


router = APIRouter()


@router.get("/api/v1/realtime")
async def realtime_availability(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    agent_id: ObjectId,
    agent_revision_id: ObjectId | None = None,
    thread_id: ObjectId | None = None,
) -> RealtimeAvailability:
    """Report browser realtime input for the selected Agent and Thread."""
    from a13n_service.runs import definitions
    from a13n_service.runs.accept import run_revision
    from a13n_service.runs.threads import get_thread

    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        authority = execution_authority(actor, scope)
        thread = await get_thread(session, scope.workspace_id, thread_id, history=False) if thread_id else None
        if thread is not None and thread.agent_snapshot is not None:
            revision = await definitions.resolve(session, thread, agent_id, agent_revision_id, None, accepting=True)
        else:
            revision, _ = await run_revision(
                session, runtime, actor, scope, agent_id, agent_revision_id, None, authority=authority
            )
        model = await resolve_model(session, actor, scope, revision.config.model, authority=authority)
    if model.config.model_api not in PROTOCOLS:
        return RealtimeAvailability()
    reason = None
    if runtime.realtime_channels is None or runtime.admission is not None:
        reason = "Realtime preview is disabled"
    elif thread is not None and (thread.archived_at is not None or thread.current_run_id is not None):
        reason = "Wait until this Thread is open and idle"
    return RealtimeAvailability(realtime=True, can_start=reason is None, accepts_text=True, reason=reason)


async def connect(socket: WebSocket, workspace_id: str) -> None:
    from a13n_service.runs.submit import create_thread, submit_message

    runtime: Runtime = socket.app.state.runtime
    channels = runtime.realtime_channels
    if channels is None or socket.headers.get("origin") not in runtime.settings.server.public_origins:
        await socket.close(code=1008)
        return
    scope = {**socket.scope, "type": "http", "method": "GET"}
    try:
        credential = await runtime.access.authenticator.authenticate(Request(scope), Response())
    except ServiceError:
        await socket.close(code=1008)
        return
    if credential is None or credential.kind != "session" or len(channels) >= runtime.settings.worker.slots:
        await socket.close(code=1008)
        return
    await socket.accept()
    session = uuid4().hex
    channel = Channel(credential.principal.id, workspace_id)
    channels[session] = channel
    try:
        async with asyncio.timeout(10):
            start = Start.model_validate_json(await socket.receive_text())
            message = Message(
                agent_id=start.agent_id,
                agent_revision_id=start.agent_revision_id,
                delivery="next_run",
                payload=MessagePayload(content=(TextPart(type="text", text="Started a realtime conversation."),)),
                options=RunOptions(labels={LABEL: session, "realtime-audio": "yes" if start.audio else "no"}),
            )
            if start.thread_id is None:
                accepted, _ = await create_thread(
                    runtime,
                    credential.principal,
                    workspace_id,
                    NewThread.model_validate(
                        {
                            **message.model_dump(),
                            "session": {
                                "labels": {"a13n.console": "debug"},
                                "origin": {"application": "a13n-console", "name": "Console"},
                            },
                        }
                    ),
                    request_key=session,
                )
            else:
                accepted, _ = await submit_message(
                    runtime, credential.principal, workspace_id, start.thread_id, message, request_key=session
                )
            if accepted.run is None:
                raise ServiceError("conflict", "Wait for the current run to finish before starting realtime")
            await socket.send_json(
                {
                    "type": "accepted",
                    "thread_id": accepted.thread.id,
                    "session_id": accepted.thread.session_id,
                    "run_id": accepted.run.id,
                }
            )
        await relay(socket, channel)
        if not channel.disconnected:
            await socket.close()
    except WebSocketDisconnect:
        pass
    except (ServiceError, ValidationError, TimeoutError) as error:
        message = error.message if isinstance(error, ServiceError) else "The realtime request could not be accepted."
        await socket.send_json({"type": "error", "message": message})
        await socket.close(code=1008)
    finally:
        channel.closed.set()
        channels.pop(session, None)


async def relay(socket: WebSocket, channel: Channel) -> None:
    async def receive() -> None:
        while True:
            event = await socket.receive()
            if event["type"] == "websocket.disconnect":
                channel.disconnected = True
                return
            if (audio := event.get("bytes")) is not None:
                if len(audio) > 16_384 or not audio or len(audio) % 2:
                    raise ServiceError("invalid_argument", "Expected a short PCM16 audio frame")
                await channel.incoming.put(audio)
            else:
                command = Input.model_validate_json(event.get("text") or "")
                if command.type == "stop":
                    return
                if command.text.strip():
                    await channel.incoming.put(command.text)

    async def send() -> None:
        while not channel.finished.is_set() or not channel.outgoing.empty():
            try:
                async with asyncio.timeout(0.25):
                    frame = await channel.outgoing.get()
            except TimeoutError:
                continue
            if isinstance(frame, bytes):
                await socket.send_bytes(frame)
            else:
                await socket.send_json(frame)

    tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        channel.closed.set()
        # A stop command keeps the socket open until the Worker commits its checkpoint.
        if tasks[0] in done and tasks[1] not in done:
            async with asyncio.timeout(15):
                await tasks[1]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
