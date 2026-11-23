"""Client transport for an all-role Service realtime preview."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import partial
from secrets import token_urlsafe
from time import monotonic
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.websocket import secure
from a13n_service.providers.realtime import PROTOCOLS
from a13n_service.resources.models.service import resolve_model
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schemas import (
    JsonPart,
    Message,
    MessagePayload,
    NewThread,
    RunOptions,
    SessionCreate,
    SessionOrigin,
)
from a13n_service.tenancy.access import Authenticated, reauthenticate, workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, execution_authority
from a13n_service.tenancy.requests import Actor, Credential, WorkspaceId

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

LABEL = "realtime-session"
INBOX_METADATA = "a13n.realtime-inbox"
BUDGET_RESTRICTION = "Realtime conversations are unavailable when per-call budget reservations are required."
SUBPROTOCOL = "a13n.realtime.v1"
TICKET_SECONDS = 30
CONNECT_PATH = "/api/v1/workspaces/{workspace_id}/realtime"
type Frame = bytes | dict[str, str | int | bool]
type Transport = Literal["websocket", "webrtc"]


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
    approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    webrtc_offer: str | None = None

    async def answer_webrtc(self, sdp: str) -> None:
        if self.closed.is_set():
            raise ServiceError("conflict", "The realtime connection closed during WebRTC setup")
        await self.outgoing.put({"type": "answer", "sdp": sdp})

    def finish(self, *, failed: bool) -> None:
        if failed and not self.outgoing.full():
            self.outgoing.put_nowait({"type": "error", "message": "The realtime run failed"})
        self.finished.set()

    async def approve(self, call_id: str, name: str, arguments: str) -> bool:
        if self.closed.is_set():
            return False
        answer = asyncio.get_running_loop().create_future()
        self.approvals[call_id] = answer
        disconnected = asyncio.create_task(self.closed.wait())
        try:
            await self.outgoing.put(
                {"type": "approval_required", "call_id": call_id, "name": name, "arguments": arguments}
            )
            done, _ = await asyncio.wait((answer, disconnected), return_when=asyncio.FIRST_COMPLETED)
            return answer in done and not self.closed.is_set() and answer.result()
        finally:
            self.approvals.pop(call_id, None)
            answer.cancel()
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)


class RealtimeSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: ObjectId
    thread_id: ObjectId | None = None
    agent_revision_id: ObjectId | None = None
    audio: bool = True
    transport: Transport = "websocket"

    @model_validator(mode="after")
    def require_webrtc_audio(self) -> RealtimeSessionCreate:
        if self.transport == "webrtc" and not self.audio:
            raise ValueError("WebRTC requires audio input")
        return self


@dataclass(frozen=True, slots=True)
class ConnectionTicket:
    workspace_id: str
    start: RealtimeSessionCreate
    credential: Authenticated
    expires: float


class RealtimeSessionTicket(BaseModel):
    websocket_path: str = Field(description="WebSocket path relative to the Service origin.")
    subprotocol: Literal["a13n.realtime.v1"] = Field(description="Offer this protocol first and the ticket second.")
    ticket: str = Field(description="Single-use connection credential. Pass it as the second WebSocket subprotocol.")
    expires_in: int = Field(description="Ticket lifetime in seconds.")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "stop"]
    text: str = Field(default="", max_length=8192)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["approval"]
    call_id: str = Field(min_length=1, max_length=256)
    approve: bool = Field(strict=True)


class WebRTCOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["offer"]
    sdp: str = Field(min_length=1, max_length=32_768)


_COMMAND = TypeAdapter(Annotated[Input | Approval, Field(discriminator="type")])


class RealtimeAvailability(BaseModel):
    realtime: bool = False
    can_start: bool = False
    accepts_text: bool = False
    reason: str | None = None
    transports: tuple[Transport, ...] = ()


router = APIRouter()


def _preview_unavailable(runtime: Runtime) -> str | None:
    if runtime.realtime_channels is None:
        return "Realtime preview is disabled"
    if runtime.admission is not None:
        return BUDGET_RESTRICTION
    return None


@router.get("/api/v1/realtime")
async def realtime_availability(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    agent_id: ObjectId,
    agent_revision_id: ObjectId | None = None,
    thread_id: ObjectId | None = None,
) -> RealtimeAvailability:
    """Report realtime input for the selected Agent and Thread."""
    availability, _ = await _inspect(
        runtime,
        actor,
        workspace_id,
        RealtimeSessionCreate(agent_id=agent_id, agent_revision_id=agent_revision_id, thread_id=thread_id),
    )
    return availability


async def _inspect(
    runtime: Runtime, actor: Principal, workspace_id: str, start: RealtimeSessionCreate
) -> tuple[RealtimeAvailability, ExecutionAuthority]:
    from a13n_service.runs import definitions
    from a13n_service.runs.accept import run_revision
    from a13n_service.runs.threads import get_thread

    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        authority = execution_authority(actor, scope)
        thread = (
            await get_thread(session, scope.workspace_id, start.thread_id, history=False)
            if start.thread_id is not None
            else None
        )
        if thread is not None and thread.agent_snapshot is not None:
            revision = await definitions.resolve(
                session, thread, start.agent_id, start.agent_revision_id, None, accepting=True
            )
        else:
            revision, _ = await run_revision(
                session, runtime, actor, scope, start.agent_id, start.agent_revision_id, None, authority=authority
            )
        model = await resolve_model(session, actor, scope, revision.config.model, authority=authority)
    protocol = PROTOCOLS.get(model.config.model_api)
    if protocol is None:
        return RealtimeAvailability(), authority
    reason = _preview_unavailable(runtime)
    if reason is None and thread is not None and (thread.archived_at is not None or thread.current_run_id is not None):
        reason = "Wait until this Thread is open and idle"
    return RealtimeAvailability(
        realtime=True,
        can_start=reason is None,
        accepts_text=True,
        reason=reason,
        transports=("websocket", "webrtc") if protocol.webrtc else ("websocket",),
    ), authority


@router.post("/api/v1/realtime/sessions", status_code=201)
async def create_realtime_session(
    runtime: CurrentRuntime, credential: Credential, workspace_id: WorkspaceId, body: RealtimeSessionCreate
) -> RealtimeSessionTicket:
    """Issue a one-use WebSocket ticket for the selected Agent and optional Thread."""
    if credential.platform is not None:
        raise ServiceError("forbidden", "Run platform credentials cannot open realtime conversations")
    if (reason := _preview_unavailable(runtime)) is not None:
        raise ServiceError("unavailable", reason)
    principal = credential.constrain(credential.principal)
    availability, authority = await _inspect(runtime, principal, workspace_id, body)
    if not availability.realtime:
        raise invalid("agent_id", "The Agent's primary model does not use a realtime API")
    if body.transport not in availability.transports:
        raise invalid("transport", "The Agent's primary model does not support WebRTC; use websocket")
    if availability.reason is not None:
        raise ServiceError("conflict", availability.reason)
    tickets = runtime.realtime_tickets
    now = monotonic()
    for key in [key for key, entry in tickets.items() if entry.expires <= now]:
        del tickets[key]
    if len(tickets) >= runtime.settings.worker.slots:
        raise ServiceError("unavailable", "Realtime connection tickets are busy; try again shortly")
    ticket = token_urlsafe(32)
    tickets[secret_hash(ticket)] = ConnectionTicket(
        workspace_id, body, replace(credential, authority=authority), now + TICKET_SECONDS
    )
    return RealtimeSessionTicket(
        websocket_path=CONNECT_PATH.format(workspace_id=workspace_id),
        subprotocol=SUBPROTOCOL,
        ticket=ticket,
        expires_in=TICKET_SECONDS,
    )


def _redeem(runtime: Runtime, socket: WebSocket, workspace_id: str, protocols: list[str]) -> ConnectionTicket:
    host = urlsplit(runtime.settings.server.public_url).hostname or ""
    local = host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")
    if (
        len(protocols) != 2
        or protocols[0] != SUBPROTOCOL
        or len(protocols[1]) != 43
        or not secure(socket, allow_loopback_ws=local and not runtime.settings.server.https)
    ):
        raise ServiceError("unauthenticated", "Invalid realtime connection ticket")
    key = secret_hash(protocols[1])
    entry = runtime.realtime_tickets.get(key)
    if entry is None or entry.expires <= monotonic() or entry.workspace_id != workspace_id:
        raise ServiceError("unauthenticated", "Invalid or expired realtime connection ticket")
    return runtime.realtime_tickets.pop(key)


async def _ticket_principal(runtime: Runtime, ticket: ConnectionTicket) -> Principal:
    async with short_session(runtime.storage) as session:
        principal = await reauthenticate(session, runtime.access, ticket.credential)
        await workspace_scope(session, principal, ticket.workspace_id, "run")
        return principal


async def connect(socket: WebSocket, workspace_id: str) -> None:
    from a13n_service.runs.submit import create_thread, submit_message

    runtime: Runtime = socket.app.state.runtime
    channels = runtime.realtime_channels
    if channels is None or runtime.admission is not None:
        await socket.close(code=1008)
        return
    protocols = socket.scope.get("subprotocols", [])
    ticket = None
    reauthorize = None
    try:
        if protocols:
            if len(channels) >= runtime.settings.worker.slots:
                raise ServiceError("unavailable", "Realtime connections are busy")
            ticket = _redeem(runtime, socket, workspace_id, protocols)
            reauthorize = partial(_ticket_principal, runtime, ticket)
            principal = await reauthorize()
        else:
            if socket.headers.get("origin") not in runtime.settings.server.public_origins:
                raise ServiceError("forbidden", "Invalid realtime connection origin")
            scope = {**socket.scope, "type": "http", "method": "GET"}
            credential = await runtime.access.authenticator.authenticate(Request(scope), Response())
            if credential is None or credential.kind != "session":
                raise ServiceError("unauthenticated", "A realtime connection requires a login session or ticket")
            principal = credential.principal
    except ServiceError:
        await socket.close(code=1008)
        return
    if len(channels) >= runtime.settings.worker.slots:
        await socket.close(code=1008)
        return
    session = uuid4().hex
    channel = Channel(principal.id, workspace_id)
    channels[session] = channel
    try:
        await socket.accept(subprotocol=SUBPROTOCOL if ticket is not None else None)
        async with asyncio.timeout(10):
            start = (
                ticket.start
                if ticket is not None
                else RealtimeSessionCreate.model_validate_json(await socket.receive_text())
            )
            if start.transport == "webrtc":
                offer = WebRTCOffer.model_validate_json(await socket.receive_text())
                lines = [line.strip() for line in offer.sdp.splitlines()]
                # Only negotiate audio. Provider data channels could bypass Service controls.
                media = [line.split() for line in lines if line.startswith("m=")]
                if (
                    not lines
                    or lines[0] != "v=0"
                    or len(media) != 1
                    or len(media[0]) < 4
                    or media[0][0] != "m=audio"
                    or media[0][2] not in {"UDP/TLS/RTP/SAVPF", "TCP/TLS/RTP/SAVPF"}
                ):
                    raise invalid("sdp", "Expected an audio-only WebRTC offer")
                channel.webrtc_offer = offer.sdp
            message = Message(
                agent_id=start.agent_id,
                agent_revision_id=start.agent_revision_id,
                delivery="next_run",
                payload=MessagePayload(
                    content=(
                        JsonPart(
                            type="json",
                            value={
                                "type": "realtime_session_requested",
                                "audio_input": start.audio,
                                "transport": start.transport,
                            },
                        ),
                    )
                ),
                options=RunOptions(labels={LABEL: session, "realtime-audio": "yes" if start.audio else "no"}),
            )
            if start.thread_id is None:
                accepted, _ = await create_thread(
                    runtime,
                    principal,
                    workspace_id,
                    NewThread(
                        **message.model_dump(),
                        session=(
                            SessionCreate(
                                labels={"a13n.console": "debug"},
                                origin=SessionOrigin(application="a13n-console", name="Console"),
                            )
                            if ticket is None
                            else None
                        ),
                    ),
                    request_key=session,
                )
            else:
                accepted, _ = await submit_message(
                    runtime, principal, workspace_id, start.thread_id, message, request_key=session
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
        await relay(socket, channel, reauthorize=reauthorize, interval=runtime.settings.worker.authority_seconds)
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


async def relay(
    socket: WebSocket,
    channel: Channel,
    *,
    reauthorize: Callable[[], Awaitable[Principal]] | None = None,
    interval: float = 1,
) -> None:
    async def receive() -> None:
        while True:
            event = await socket.receive()
            if event["type"] == "websocket.disconnect":
                channel.disconnected = True
                return
            if (audio := event.get("bytes")) is not None:
                if channel.webrtc_offer is not None:
                    raise invalid("audio", "Send audio over the WebRTC peer connection")
                if len(audio) > 16_384 or not audio or len(audio) % 2:
                    raise ServiceError("invalid_argument", "Expected a short PCM16 audio frame")
                await channel.incoming.put(audio)
            else:
                command = _COMMAND.validate_json(event.get("text") or "")
                if isinstance(command, Approval):
                    answer = channel.approvals.get(command.call_id)
                    if answer is not None and not answer.done():
                        if reauthorize is not None and command.approve:
                            await reauthorize()
                        if not answer.done():
                            answer.set_result(command.approve)
                    continue
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

    async def verify(authorize: Callable[[], Awaitable[Principal]]) -> None:
        while True:
            await asyncio.sleep(interval)
            await authorize()

    tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
    if reauthorize is not None:
        tasks.append(asyncio.create_task(verify(reauthorize)))
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
