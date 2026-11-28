"""Client ingress and attempt-side channel for Service realtime conversations."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import timedelta
from functools import partial
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from a13n_stream_protocol.realtime import SpeechTranscript
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select
from starlette.websockets import WebSocketState

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.infra.ids import Key, ObjectId, new_object_id
from a13n_service.infra.telemetry import meter
from a13n_service.infra.websocket import secure
from a13n_service.providers.realtime import PROTOCOLS
from a13n_service.resources.agents import stored
from a13n_service.resources.agents.schemas import AgentOverride
from a13n_service.resources.memories.schemas import MemoryMounts, SessionMemorySettings
from a13n_service.resources.models.service import resolve_model
from a13n_service.runs.admission import requires_model_reservation
from a13n_service.runs.attempts import Lease, prove
from a13n_service.runs.realtime_transport import (
    CONTROL_OVERFLOWS,
    HEARTBEAT_SECONDS,
    PCM_DISCONTINUITIES,
    AttachTimeout,
    Offer,
    Pipe,
    TransportError,
    unavailable,
)
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schemas import (
    InitialMounts,
    RealtimeCompletionReason,
    RunOptions,
    SessionCreate,
    SessionOrigin,
    UsageLimit,
)
from a13n_service.runs.tables import AttemptRow, RealtimeCallRow, RealtimeTicketRow, RunRow, ThreadRow
from a13n_service.runs.threads import get_run
from a13n_service.tenancy.access import Authenticated, reauthenticate, workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, execution_authority
from a13n_service.tenancy.requests import Actor, Credential, WorkspaceId
from a13n_service.tenancy.tables import WorkspaceRow

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

INBOX_METADATA = "a13n.realtime-inbox"
BUDGET_RESTRICTION = "Realtime conversations are unavailable when per-call budget reservations are required."
SUBPROTOCOL = "a13n.realtime.v1"
TICKET_SECONDS = 30
CONNECT_PATH = "/api/v1/workspaces/{workspace_id}/realtime"
type Frame = bytes | dict[str, str | int | bool]
type Transport = Literal["websocket", "webrtc"]
type HandshakeResult = Literal["ready", "timeout", "failed", "abandoned"]
type LimitReason = RealtimeCompletionReason | Literal["usage_limit_exceeded"]
LIMIT_REASONS = frozenset(("duration_limit", "idle_timeout", "usage_limit_exceeded"))
type CallEnd = Literal["completed", "client_disconnected", "draining", "transport_error", "failed", "limit_exceeded"]

ACTIVE_CONNECTIONS = meter.create_up_down_counter(
    "a13n.realtime.connections.active", unit="{connection}", description="Open realtime signaling WebSockets"
)
HANDSHAKES = meter.create_histogram(
    "a13n.realtime.handshakes.duration",
    unit="s",
    description="Realtime WebSocket accept to ready or aborted handshake",
    explicit_bucket_boundaries_advisory=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30),
)
CALLS_ENDED = meter.create_counter("a13n.realtime.calls.ended", unit="{call}", description="Ended realtime WebSockets")


@dataclass(slots=True)
class _ConnectionObservation:
    transport: Transport
    started: float = field(default_factory=lambda: asyncio.get_running_loop().time())
    handshake_recorded: bool = False
    reason: CallEnd = "failed"
    terminal_reason: CallEnd | None = None

    def handshake(self, result: HandshakeResult) -> None:
        if not self.handshake_recorded:
            duration = asyncio.get_running_loop().time() - self.started
            HANDSHAKES.record(duration, {"transport": self.transport, "result": result})
            self.handshake_recorded = True


def _transcript_frame(update: SpeechTranscript) -> dict[str, str | int | bool]:
    return {
        "type": "transcript",
        "message_id": update["message_id"],
        "role": update["role"],
        "text": update["text"],
        "final": update["final"],
    }


@dataclass(slots=True)
class Channel:
    principal_id: str
    workspace_id: str
    incoming: asyncio.Queue[bytes | str | Media] = field(default_factory=lambda: asyncio.Queue(64))
    outgoing: asyncio.Queue[dict[str, str | int | bool]] = field(default_factory=lambda: asyncio.Queue(512))
    pcm: asyncio.Queue[tuple[int, bytes]] = field(default_factory=lambda: asyncio.Queue(32))
    decisions: dict[str, bool | None] = field(default_factory=dict)
    dispatched: set[str] = field(default_factory=set)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    failed: bool = False
    attached: bool = False
    audio: bool = True
    draft: str = ""
    playback_epoch: int = 0
    playback_offset: int = 0
    verify_dispatch: Callable[[str], Awaitable[bool]] | None = None
    approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    webrtc_offer: str | None = None
    transcript_window: float = 0
    max_duration_seconds: int | None = None
    end_reason: LimitReason | None = field(default=None, init=False)
    draining: bool = field(default=False, init=False)
    foreground: set[str] = field(default_factory=set, init=False)
    last_activity: float = field(default=0, init=False)
    activity: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _transcripts: dict[tuple[str, str], SpeechTranscript] = field(default_factory=dict, init=False)
    _transcript_timer: asyncio.TimerHandle | None = field(default=None, init=False)

    async def answer_webrtc(self, sdp: str) -> None:
        if self.closed.is_set():
            raise ServiceError("conflict", "The realtime connection closed during WebRTC setup")
        self.emit({"type": "answer", "sdp": sdp})

    def emit(self, frame: Frame) -> None:
        if isinstance(frame, bytes):
            if self.pcm.full():
                self.pcm.get_nowait()
            self.pcm.put_nowait((self.playback_epoch, self.playback_offset.to_bytes(8) + frame))
            self.playback_offset += len(frame)
        else:
            self.flush_transcripts()
            self._enqueue(frame)

    def transcript(self, update: SpeechTranscript) -> None:
        key = (update["message_id"], update["role"])
        self._transcripts.pop(key, None)
        if update["final"] or not self.transcript_window:
            self.emit(_transcript_frame(update))
            return
        # Flush retained snapshots in order of their latest update.
        self._transcripts[key] = update
        if self._transcript_timer is None:
            self._transcript_timer = asyncio.get_running_loop().call_later(self.transcript_window, self._flush_on_timer)

    def flush_transcripts(self) -> None:
        if self._transcript_timer is None:
            return
        self._transcript_timer.cancel()
        self._transcript_timer = None
        pending, self._transcripts = self._transcripts, {}
        for update in pending.values():
            self._enqueue(_transcript_frame(update))

    def _flush_on_timer(self) -> None:
        with suppress(TransportError):
            self.flush_transcripts()  # Queue overflow has already closed the channel.

    def _enqueue(self, frame: dict[str, str | int | bool]) -> None:
        try:
            self.outgoing.put_nowait(frame)
        except asyncio.QueueFull:
            self._overflow()
            raise TransportError("The realtime client is not keeping up") from None

    def _overflow(self) -> None:
        if not self.failed:
            CONTROL_OVERFLOWS.add(1, {"side": "worker"})
        self.failed = True
        self.closed.set()

    def touch(self) -> None:
        self.last_activity = asyncio.get_running_loop().time()
        self.activity.set()

    def end_for_limit(self, reason: LimitReason) -> None:
        closing = self.closed.is_set()
        # A charged turn can be refused while the channel is already closing. Keep that verdict for the seal.
        if not closing or reason == "usage_limit_exceeded":
            self.end_reason = reason
        if not closing:
            self.emit({"type": "ending", "reason": reason})
            # Keep the outbound lane open so the Worker can deliver the committed seal.
            self.closed.set()

    def finish(self, run_id: str, status: str, failure_code: str | None = None) -> None:
        self.flush_transcripts()
        reason = failure_code or (self.end_reason if status == "completed" else None)
        if reason in {"forbidden", "unauthenticated"}:
            reason = "authority_revoked"
        if reason is None and self.draining:
            reason = "worker_draining"
        if status == "failed" and self.outgoing.qsize() + 2 <= self.outgoing.maxsize:
            self._enqueue(
                {"type": "error", "code": reason or "realtime_unavailable", "message": "The realtime run failed"}
            )
        frame: dict[str, str | int | bool] = {"type": "sealed", "run_id": run_id, "status": status}
        if reason is not None:
            frame["reason"] = reason
        self.emit(frame)
        self.finished.set()

    async def approve(self, call_id: str, name: str, arguments: str) -> bool | None:
        if call_id in self.decisions:
            return False
        self.decisions[call_id] = None
        # A tool body or managed policy may defer an execution that already reached the dispatch hook.
        self.dispatched.discard(call_id)
        if self.closed.is_set():
            return None
        answer = asyncio.get_running_loop().create_future()
        self.approvals[call_id] = answer
        disconnected = asyncio.create_task(self.closed.wait())
        try:
            self.emit({"type": "approval_required", "call_id": call_id, "name": name, "arguments": arguments})
            done, _ = await asyncio.wait((answer, disconnected), return_when=asyncio.FIRST_COMPLETED)
            if self.closed.is_set() or answer not in done:
                return None
            self.decisions[call_id] = answer.result()
            return answer.result()
        finally:
            self.approvals.pop(call_id, None)
            answer.cancel()
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)


class RealtimeSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: ObjectId
    thread_id: ObjectId | None = None
    fork_run_id: ObjectId | None = None
    fresh_environments: bool = False
    agent_revision_id: ObjectId | None = None
    audio: bool = True
    transport: Transport = "websocket"
    max_usage: UsageLimit | None = None
    options: RunOptions = Field(default_factory=RunOptions)
    memory: SessionMemorySettings | None = None
    memories: MemoryMounts = ()
    environments: InitialMounts = ()
    draft: str = Field(default="", max_length=8192)
    max_duration_seconds: int | None = Field(default=None, ge=30)

    @model_validator(mode="after")
    def require_webrtc_audio(self) -> RealtimeSessionCreate:
        if self.fork_run_id and self.thread_id:
            raise ValueError("Choose an existing Thread or a Run to fork")
        if self.fresh_environments and not self.fork_run_id:
            raise ValueError("Fresh environments require a fork")
        if (self.thread_id or self.fork_run_id) and (self.memory is not None or self.environments or self.memories):
            raise ValueError("Session memory and initial mounts require a new conversation")
        if self.options.context or self.options.configuration:
            raise ValueError("Realtime tickets do not accept request headers or model routing configuration")
        if self.max_usage and self.options.max_usage:
            raise ValueError("Choose a single request limit")
        if self.fork_run_id and (self.max_usage is not None or self.options != RunOptions()):
            raise ValueError("A fork inherits the source Run options and usage limit")
        if self.transport == "webrtc" and not self.audio:
            raise ValueError("WebRTC requires audio input")
        return self


class ConnectionTicket(BaseModel):
    run_id: str
    thread_id: str
    session_id: str
    start: RealtimeSessionCreate
    credential: Authenticated


class IceServer(BaseModel):
    urls: str | tuple[str, ...]
    username: str | None = None
    credential: str | None = Field(default=None, repr=False)


class RealtimeSessionTicket(BaseModel):
    maturity: Literal["alpha"] = "alpha"
    audio_input_hz: int
    audio_output_hz: int
    max_duration_seconds: int
    ice_servers: tuple[IceServer, ...] = ()
    run_id: str
    thread_id: str
    session_id: str
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


class Media(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["interrupt", "audio_commit", "input_discontinuity"]
    played_bytes: int = Field(default=0, ge=0)


class WebRTCOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["offer"]
    sdp: str = Field(min_length=1, max_length=32_768)


_COMMAND = TypeAdapter(Annotated[Input | Approval | Media, Field(discriminator="type")])
_AUTH = TypeAdapter(Authenticated)
_OUTPUT = TypeAdapter(dict[str, str | int | bool])


class RealtimeAvailability(BaseModel):
    realtime: bool = False
    can_start: bool = False
    maturity: Literal["alpha"] = "alpha"
    accepts_text: bool = False
    accepts_images: bool = False
    supported_image_types: tuple[str, ...] = ()
    audio_input_hz: int | None = None
    audio_output_hz: int | None = None
    supports_interruption: bool = False
    max_duration_seconds: int | None = None
    reason: str | None = None
    transports: tuple[Transport, ...] = ()


router = APIRouter()


def _unavailable_reason(runtime: Runtime) -> str | None:
    if runtime.realtime_bridge is None:
        return "Realtime ingress is unavailable"
    if requires_model_reservation(runtime.admission):
        return BUDGET_RESTRICTION
    return None


@router.get("/api/v1/realtime", summary="Check Realtime availability (Alpha)")
async def realtime_availability(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    agent_id: ObjectId,
    agent_revision_id: ObjectId | None = None,
    thread_id: ObjectId | None = None,
    fork_run_id: ObjectId | None = None,
    model: Key | None = None,
) -> RealtimeAvailability:
    """Report Alpha native live API capabilities for the selected Agent and Thread."""
    if thread_id and fork_run_id:
        raise invalid("fork_run_id", "Choose an existing Thread or a Run to fork")
    if fork_run_id and model is not None:
        raise invalid("model", "A fork inherits the source Run model")
    availability, _ = await _inspect(
        runtime,
        actor,
        workspace_id,
        RealtimeSessionCreate(
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            thread_id=thread_id,
            fork_run_id=fork_run_id,
            options=RunOptions(overrides=AgentOverride(model=model)) if model else RunOptions(),
        ),
    )
    return availability


async def _inspect(
    runtime: Runtime, actor: Principal, workspace_id: str, start: RealtimeSessionCreate
) -> tuple[RealtimeAvailability, ExecutionAuthority]:
    from a13n_service.runs import definitions
    from a13n_service.runs.accept import eligible, run_revision
    from a13n_service.runs.threads import get_thread

    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        authority = execution_authority(actor, scope)
        origin = await get_run(session, workspace_id, start.fork_run_id) if start.fork_run_id else None
        thread_id = origin.thread_id if origin else start.thread_id
        thread = await get_thread(session, workspace_id, thread_id, history=False) if thread_id else None
        options = start.options
        revision_id = start.agent_revision_id
        if origin:
            if origin.trigger != "realtime" or not origin.sealed_at:
                raise invalid("fork_run_id", "Choose a sealed realtime Run")
            if origin.agent_id != start.agent_id or (
                revision_id is not None and origin.agent_revision_id != revision_id
            ):
                raise invalid("agent_id", "A realtime fork uses the source Run's Agent revision")
            revision_id = origin.agent_revision_id
            options = stored.require(RunOptions, origin.options, kind="run", id=origin.id)
        if thread is not None and thread.agent_snapshot is not None:
            revision = await definitions.resolve(
                session, thread, start.agent_id, revision_id, options.overrides, accepting=True
            )
        else:
            revision, _ = await run_revision(
                session,
                runtime,
                actor,
                scope,
                start.agent_id,
                revision_id,
                options.overrides,
                authority=authority,
            )
        last = await session.get(RunRow, thread.last_run_id) if thread and thread.last_run_id and not origin else None
        effective = stored.require_effective(revision.config, options.overrides, revision_id=revision.definition_id)
        model = await resolve_model(session, actor, scope, effective.model, authority=authority)
    protocol = PROTOCOLS.get(model.config.model_api)
    if protocol is None:
        return RealtimeAvailability(), authority
    protocol = protocol.for_model(model.config.model_name)
    reason = _unavailable_reason(runtime)
    if reason is None and start.thread_id and thread is not None and not eligible(thread, last):
        reason = "Wait until this Thread is open and idle"
    return RealtimeAvailability(
        realtime=True,
        can_start=reason is None,
        accepts_text=True,
        audio_input_hz=protocol.audio_input_hz,
        audio_output_hz=protocol.audio_output_hz,
        supports_interruption=protocol.supports_interruption,
        max_duration_seconds=runtime.settings.worker.realtime_max_duration_seconds,
        reason=reason,
        transports=("websocket", "webrtc") if protocol.webrtc else ("websocket",),
    ), authority


@router.post("/api/v1/realtime/sessions", status_code=201, summary="Create a Realtime session ticket (Alpha)")
async def create_realtime_session(
    runtime: CurrentRuntime, credential: Credential, workspace_id: WorkspaceId, body: RealtimeSessionCreate
) -> RealtimeSessionTicket:
    """Issue a single-use ticket for the Alpha Realtime WebSocket at `/api/v1/workspaces/{workspace_id}/realtime`."""
    if credential.platform is not None:
        raise ServiceError("forbidden", "Run platform credentials cannot open realtime conversations")
    if type(credential) is not Authenticated:
        raise ServiceError("unavailable", "This credential type does not support realtime tickets")
    if (reason := _unavailable_reason(runtime)) is not None:
        raise ServiceError("unavailable", reason)
    principal = credential.constrain(credential.principal)
    availability, authority = await _inspect(runtime, principal, workspace_id, body)
    if not availability.realtime:
        raise invalid("agent_id", "The Agent's primary model does not use a realtime API")
    if body.transport not in availability.transports:
        raise invalid("transport", "The Agent's primary model does not support WebRTC; use websocket")
    if availability.reason is not None:
        raise ServiceError("conflict", availability.reason)
    ceiling = runtime.settings.worker.realtime_max_duration_seconds
    max_duration = body.max_duration_seconds or ceiling
    if max_duration > ceiling:
        raise invalid("max_duration_seconds", f"The configured maximum is {ceiling} seconds")
    body = body.model_copy(update={"max_duration_seconds": max_duration})
    token = token_urlsafe(32)
    async with transaction(runtime.storage) as session:
        await session.get_one(WorkspaceRow, workspace_id, with_for_update={"key_share": True})
        current = await now(session)
        await session.execute(
            delete(RealtimeTicketRow).where(
                RealtimeTicketRow.workspace_id == workspace_id,
                RealtimeTicketRow.expires_at <= current,
            )
        )
        outstanding = await session.scalar(
            select(func.count()).select_from(RealtimeTicketRow).where(RealtimeTicketRow.workspace_id == workspace_id)
        )
        if (outstanding or 0) >= runtime.settings.worker.realtime_ticket_limit:
            raise ServiceError("unavailable", "Realtime connection tickets are busy")
        if body.fork_run_id:
            origin = await get_run(session, workspace_id, body.fork_run_id)
            session_id = origin.session_id
            body = body.model_copy(update={"agent_revision_id": origin.agent_revision_id})
        elif body.thread_id:
            session_id = (await session.get_one(ThreadRow, body.thread_id)).session_id
        else:
            session_id = new_object_id("sess")
        value = ConnectionTicket(
            start=body,
            credential=replace(credential, authority=authority),
            session_id=session_id,
            thread_id=body.thread_id or new_object_id("thread"),
            run_id=new_object_id("run"),
        )
        session.add(
            RealtimeTicketRow(
                digest=secret_hash(token),
                workspace_id=workspace_id,
                expires_at=current + timedelta(seconds=TICKET_SECONDS),
                request=value.model_dump(mode="json", exclude_unset=True),
            )
        )
    assert availability.audio_input_hz is not None and availability.audio_output_hz is not None
    return RealtimeSessionTicket(
        audio_input_hz=availability.audio_input_hz,
        audio_output_hz=availability.audio_output_hz,
        max_duration_seconds=max_duration,
        ice_servers=tuple(
            IceServer(
                urls=server.urls,
                username=server.username,
                credential=server.credential.get_secret_value() if server.credential is not None else None,
            )
            for server in runtime.settings.control.realtime_ice_servers
        )
        if body.transport == "webrtc"
        else (),
        websocket_path=CONNECT_PATH.format(workspace_id=workspace_id),
        subprotocol=SUBPROTOCOL,
        ticket=token,
        session_id=value.session_id,
        run_id=value.run_id,
        thread_id=value.thread_id,
        expires_in=TICKET_SECONDS,
    )


async def _lookup(runtime: Runtime, socket: WebSocket, workspace_id: str, protocols: list[str]) -> ConnectionTicket:
    host = urlsplit(runtime.settings.server.public_url).hostname or ""
    local = host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")
    if (
        len(protocols) != 2
        or protocols[0] != SUBPROTOCOL
        or len(protocols[1]) != 43
        or not secure(socket, allow_loopback_ws=local and not runtime.settings.server.https)
    ):
        raise ServiceError("unauthenticated", "Invalid realtime connection ticket")
    async with short_session(runtime.storage) as session:
        raw = await session.scalar(
            select(RealtimeTicketRow.request).where(
                RealtimeTicketRow.digest == secret_hash(protocols[1]),
                RealtimeTicketRow.workspace_id == workspace_id,
                RealtimeTicketRow.expires_at > func.clock_timestamp(),
            )
        )
    if raw is None:
        raise ServiceError("unauthenticated", "Invalid or expired realtime connection ticket")
    return ConnectionTicket.model_validate(raw)


async def _redeem(runtime: Runtime, workspace_id: str, token: str, ticket: ConnectionTicket) -> None:
    from a13n_service.runs import definitions
    from a13n_service.runs.accept import (
        Source,
        eligible,
        require_message_source,
        run_revision,
        snapshot_options,
        start_run,
    )
    from a13n_service.runs.environments.mounts import mount_environments
    from a13n_service.runs.memories.mounts import mount_memories
    from a13n_service.runs.memories.session_memory import configure
    from a13n_service.runs.sessions import new_session
    from a13n_service.runs.submit import fork_thread
    from a13n_service.runs.threads import get_thread, new_thread

    start = ticket.start
    if reason := _unavailable_reason(runtime):
        raise ServiceError("unavailable", reason)
    async with transaction(runtime.storage) as session:
        await session.get_one(WorkspaceRow, workspace_id, with_for_update={"key_share": True})
        redeemed = await session.scalar(
            delete(RealtimeTicketRow)
            .where(
                RealtimeTicketRow.digest == secret_hash(token),
                RealtimeTicketRow.workspace_id == workspace_id,
                RealtimeTicketRow.expires_at > func.clock_timestamp(),
            )
            .returning(RealtimeTicketRow.digest)
        )
        if redeemed is None:
            raise ServiceError("unauthenticated", "Invalid or expired realtime connection ticket")
        actor = await reauthenticate(session, runtime.access, ticket.credential)
        scope = await workspace_scope(session, actor, workspace_id, "run")
        authority = execution_authority(actor, scope)
        active, queued, pcm = (
            await session.execute(
                select(
                    func.count(),
                    func.count().filter(RunRow.status == "accepted"),
                    func.count().filter(RealtimeCallRow.transport == "websocket"),
                )
                .select_from(RunRow)
                .join(RealtimeCallRow, RealtimeCallRow.run_id == RunRow.id)
                .where(RunRow.workspace_id == workspace_id, RunRow.status.in_(("accepted", "running")))
            )
        ).one()
        limits = runtime.settings.worker
        if (
            active >= limits.realtime_call_limit
            or queued >= limits.realtime_accepted_limit
            or (start.transport == "websocket" and pcm >= limits.realtime_pcm_limit)
        ):
            raise ServiceError("unavailable", "Workspace realtime call limit reached")
        options = start.options.model_copy(update={"max_usage": start.max_usage}) if start.max_usage else start.options
        if start.fork_run_id:
            origin = await get_run(session, workspace_id, start.fork_run_id)
            thread = await fork_thread(
                session,
                runtime,
                actor,
                scope,
                origin,
                start.agent_id,
                start.agent_revision_id,
                fresh_environments=start.fresh_environments,
                thread_id=ticket.thread_id,
            )
            options = stored.require(RunOptions, origin.options, kind="run", id=origin.id)
        elif start.thread_id:
            thread = await get_thread(session, workspace_id, start.thread_id, lock="acceptance")
            last = await session.get(RunRow, thread.last_run_id) if thread.last_run_id else None
            if not eligible(thread, last):
                raise ServiceError("conflict", "Wait until this Thread is open and idle")
        else:
            details = (
                SessionCreate(
                    labels={"a13n.console": "debug"}, origin=SessionOrigin(application="a13n-console", name="Console")
                )
                if ticket.credential.kind == "session"
                else None
            )
            owner = new_session(scope.organization_id, workspace_id, actor.id, details)
            owner.id = ticket.session_id
            thread = new_thread(owner, mcp_headers={})
            thread.id = ticket.thread_id
            session.add_all((owner, thread))
            await session.flush()
            if start.memory is not None:
                await configure(session, owner, start.memory)
            await mount_environments(session, thread, start.environments, principal=actor)
            await mount_memories(session, thread, start.memories, limit=runtime.settings.memory.mounts_per_thread)
        await require_message_source(session, thread, start.agent_id)
        if thread.agent_snapshot is not None:
            revision = await definitions.resolve(
                session, thread, start.agent_id, start.agent_revision_id, options.overrides, accepting=True
            )
            options = await snapshot_options(session, thread, options)
        else:
            revision, overrides = await run_revision(
                session,
                runtime,
                actor,
                scope,
                start.agent_id,
                start.agent_revision_id,
                options.overrides,
                authority=authority,
            )
            options = options.model_copy(update={"overrides": overrides})
        await start_run(
            session,
            runtime,
            thread,
            Source(
                trigger="realtime",
                principal=actor,
                authority=authority,
                revision=revision,
                revision_selection="snapshot"
                if thread.agent_snapshot is not None
                else "pinned"
                if start.agent_revision_id
                else "default",
                options=options,
                options_digest=options.digest(),
            ),
            preallocated_id=ticket.run_id,
            realtime_transport=start.transport,
        )
        session.add(
            RealtimeCallRow(
                run_id=ticket.run_id, transport=start.transport, credential=ticket.model_dump(mode="json")["credential"]
            )
        )


async def _connection_principal(runtime: Runtime, credential: Authenticated, workspace_id: str) -> Principal:
    async with short_session(runtime.storage) as session:
        principal = await reauthenticate(session, runtime.access, credential)
        await workspace_scope(session, principal, workspace_id, "run")
        return principal


def _error_code(error: RedisError | ServiceError | ValidationError | TimeoutError) -> str:
    if isinstance(error, (RedisError, TransportError, TimeoutError)):
        return "realtime_transport_failed"
    if isinstance(error, ValidationError):
        return "invalid_argument"
    if error.code in {"forbidden", "unauthenticated", "not_found"}:
        return "authority_revoked"
    if error.code == "unavailable":
        return "realtime_unavailable"
    return error.code


async def connect(socket: WebSocket, workspace_id: str) -> None:
    """Redeem a ticket and relay Alpha Realtime frames."""
    runtime: Runtime = socket.app.state.runtime
    bridge = runtime.realtime_bridge
    protocols = socket.scope.get("subprotocols", [])
    try:
        if bridge is None:
            raise unavailable()
        ticket = await _lookup(runtime, socket, workspace_id, protocols)
        await _connection_principal(runtime, ticket.credential, workspace_id)
        await bridge.start()
        await bridge.redis.ping()
    except (RedisError, ServiceError) as error:
        status = 401 if isinstance(error, ServiceError) and error.code == "unauthenticated" else 503
        await socket.send_denial_response(
            JSONResponse({"error": "Realtime connection unavailable"}, status_code=status)
        )
        return
    reauthorize = partial(_connection_principal, runtime, ticket.credential, workspace_id)
    session = ticket.run_id
    webrtc_offer = None
    pipe = Pipe(bridge, session, side="control")
    run_id = None
    finished = False
    stack = AsyncExitStack()
    observation = None
    start = ticket.start
    try:
        await socket.accept(subprotocol=SUBPROTOCOL)
        observation = _ConnectionObservation(start.transport)
        ACTIVE_CONNECTIONS.add(1, {"transport": start.transport})
        async with asyncio.timeout(10):
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
                webrtc_offer = offer.sdp
            await _redeem(runtime, workspace_id, protocols[1], ticket)
            run_id = ticket.run_id
            stack.push_async_callback(pipe.shutdown)
            await stack.enter_async_context(pipe.subscribe())
            await pipe.publish_offer(
                Offer(
                    control_id=bridge.id,
                    draft=start.draft,
                    principal_id=ticket.credential.principal.id,
                    workspace_id=workspace_id,
                    audio=start.audio,
                    max_duration_seconds=start.max_duration_seconds,
                )
            )
            await socket.send_json(
                {"type": "accepted", "thread_id": ticket.thread_id, "session_id": ticket.session_id, "run_id": run_id}
            )
        disconnected = await relay(
            socket,
            pipe,
            offer=webrtc_offer,
            authorize_attachment=partial(_authorize_attachment, runtime, workspace_id, run_id),
            reauthorize=reauthorize,
            interval=runtime.settings.worker.authority_seconds,
            observation=observation,
        )
        finished = not disconnected
        if finished:
            await socket.close()
    except (RedisError, ServiceError, ValidationError, TimeoutError) as error:
        if observation is not None and not finished:
            if isinstance(error, (RedisError, TransportError, TimeoutError)):
                observation.reason = "transport_error"
            if isinstance(error, (AttachTimeout, TimeoutError)) and observation.terminal_reason is None:
                observation.handshake("timeout")
        message = error.message if isinstance(error, ServiceError) else "The realtime request could not be accepted."
        if socket.client_state == socket.application_state == WebSocketState.CONNECTED:
            try:
                await socket.send_json({"type": "error", "code": _error_code(error), "message": message})
                await socket.close(code=1008)
            except (WebSocketDisconnect, OSError):
                pass
    except (WebSocketDisconnect, OSError) as error:
        if observation is not None and not finished:
            observation.reason = (
                "draining" if isinstance(error, WebSocketDisconnect) and error.code == 1012 else "client_disconnected"
            )
    except asyncio.CancelledError:
        if observation is not None and not finished:
            observation.reason = "draining"
        raise
    finally:
        if observation is not None:
            reason = observation.terminal_reason or observation.reason
            result = "abandoned" if reason in {"client_disconnected", "draining"} else "failed"
            observation.handshake(result)
            CALLS_ENDED.add(1, {"transport": start.transport, "reason": reason})
            ACTIVE_CONNECTIONS.add(-1, {"transport": start.transport})
        try:
            await stack.aclose()
            if run_id is not None:
                await pipe.discard_offer()
        finally:
            if run_id is not None and not finished:
                await _stop_abandoned(runtime, workspace_id, run_id)


async def _stop_abandoned(runtime: Runtime, workspace_id: str, run_id: str) -> None:
    from a13n_service.runs.schemas import Outcome
    from a13n_service.runs.seal import seal
    from a13n_service.runs.threads import get_run, get_thread

    async with transaction(runtime.storage) as session:
        run = await get_run(session, workspace_id, run_id)
        thread = await get_thread(session, workspace_id, run.thread_id, lock=True)
        run = await get_run(session, workspace_id, run_id, lock=True)
        if run.status == "accepted":
            await seal(
                session,
                runtime,
                thread,
                run,
                None,
                Outcome.failed("realtime_unavailable", "Realtime Worker did not attach"),
                at=await now(session),
            )


async def _authorize_attachment(runtime: Runtime, workspace_id: str, run_id: str, attempt_id: str) -> None:
    async with short_session(runtime.storage) as session:
        current = await session.scalar(
            select(AttemptRow.id)
            .join(RunRow, RunRow.current_attempt_id == AttemptRow.id)
            .where(
                RunRow.id == run_id,
                RunRow.workspace_id == workspace_id,
                RunRow.status == "running",
                AttemptRow.id == attempt_id,
                AttemptRow.status == "leased",
                AttemptRow.lease_expires_at > func.now(),
            )
        )
    if current is None:
        raise ServiceError("conflict", "The realtime Worker no longer owns this Run")


async def relay(
    socket: WebSocket,
    pipe: Pipe,
    *,
    offer: str | None,
    authorize_attachment: Callable[[str], Awaitable[None]],
    reauthorize: Callable[[], Awaitable[Principal]],
    interval: float,
    observation: _ConnectionObservation,
) -> bool:
    """Return whether the client disconnected. A normal stop waits for the Worker to seal."""
    attached = asyncio.Event()
    ready = asyncio.Event()

    # Pub/Sub PCM may arrive before ordered ready or interruption frames.
    waiting_pcm: deque[bytes] = deque(maxlen=32)

    async def pulse() -> None:
        await attached.wait()
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await pipe.send(b"ping")

    async def receive() -> CallEnd | None:
        while True:
            event = await socket.receive()
            if event["type"] == "websocket.disconnect":
                return "draining" if event.get("code") == 1012 else "client_disconnected"
            if (audio := event.get("bytes")) is not None:
                if not ready.is_set():
                    raise invalid("audio", "Wait for realtime ready")
                if offer is not None:
                    raise invalid("audio", "Send audio over the WebRTC peer connection")
                if len(audio) > 16_384 or not audio or len(audio) % 2:
                    raise invalid("audio", "Expected a short PCM16 audio frame")
                frame = b"b" + audio
            else:
                command = _COMMAND.validate_json(event.get("text") or "")
                if isinstance(command, Input) and command.type == "stop":
                    return None
                if not ready.is_set():
                    raise invalid("type", "Wait for realtime ready")
                if isinstance(command, Media):
                    if command.type == "input_discontinuity":
                        raise invalid("type", "This event is server-generated")
                    if command.type == "audio_commit":
                        if offer is not None:
                            raise invalid("audio", "Audio commit requires a ready PCM stream")
                        await pipe.send(b"commit:" + str(pipe.sent).encode())
                        continue
                    pipe.epoch += 1
                    waiting_pcm.clear()
                if isinstance(command, Approval):
                    await reauthorize()
                frame = b"j" + command.model_dump_json().encode()
            await pipe.send(frame)

    async def send() -> None:
        async with pipe.handshake():
            attachment = await pipe.receive()
            if not attachment.startswith(b"a"):
                raise unavailable()
            await authorize_attachment(pipe.attempt_id)
            await pipe.send(b"o" + (offer or "").encode())
        attached.set()
        next_offset = 0
        acknowledged_epoch = 0

        async def play(raw: bytes) -> None:
            nonlocal next_offset
            offset = int.from_bytes(raw[1:9])
            if offset != next_offset:
                PCM_DISCONTINUITIES.add(1, {"direction": "egress"})
                await socket.send_json({"type": "playback_position", "offset": offset})
            await socket.send_bytes(raw[9:])
            next_offset = offset + len(raw) - 9

        while True:
            raw = await pipe.receive()
            if raw == b"closed":
                raise unavailable()
            if raw == b"finish":
                return
            if raw == b"ping":
                continue
            async with asyncio.timeout(5):
                if raw.startswith(b"b"):
                    if ready.is_set() and acknowledged_epoch == pipe.epoch:
                        await play(raw)
                    else:
                        waiting_pcm.append(raw)
                else:
                    event = _OUTPUT.validate_json(raw[1:])
                    kind = event.get("type")
                    if kind == "ready":
                        ready.set()
                    await socket.send_json(event)
                    if kind == "ready":
                        observation.handshake("ready")
                    if kind == "draining":
                        observation.terminal_reason = "draining"
                    elif kind == "ending" and event.get("reason") in LIMIT_REASONS:
                        observation.terminal_reason = "limit_exceeded"
                    if kind == "sealed":
                        # The seal follows the commit. Its delivery can race the bridge's separate finish marker.
                        if observation.terminal_reason == "draining":
                            observation.handshake("abandoned")
                        else:
                            reason = event.get("reason")
                            if reason in LIMIT_REASONS:
                                observation.terminal_reason = "limit_exceeded"
                            elif reason == "realtime_transport_failed":
                                observation.terminal_reason = "transport_error"
                            else:
                                observation.terminal_reason = (
                                    "failed" if event.get("status") == "failed" else "completed"
                                )
                            observation.handshake("failed")
                    if kind == "interrupted":
                        acknowledged_epoch += 1
                    if ready.is_set() and kind in {"ready", "interrupted"}:
                        while waiting_pcm and acknowledged_epoch == pipe.epoch:
                            await play(waiting_pcm.popleft())

    async def verify() -> None:
        while True:
            await asyncio.sleep(interval)
            await reauthorize()

    reader = asyncio.create_task(receive())
    writer = asyncio.create_task(send())
    tasks = (reader, writer, asyncio.create_task(pulse()), asyncio.create_task(verify()))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if reader in done and not reader.cancelled() and reader.exception() is None and (reason := reader.result()):
            observation.reason = reason
            return True
        if writer in done:
            # A terminal frame follows the committed outcome. The Worker can unsubscribe before our next ping.
            writer.result()
            observation.reason = "completed"
            return False
        for task in done:
            task.result()
        if reader not in done:
            return False
        if not writer.done():
            async with asyncio.timeout(15):
                if attached.is_set():
                    await pipe.send(b"stop")
                else:
                    raise ServiceError("conflict", "The realtime Worker has not attached")
                await writer
        writer.result()
        observation.reason = "completed"
        return False
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def worker_channel(runtime: Runtime, lease: Lease) -> AsyncIterator[Channel]:
    bridge, session_id = runtime.realtime_bridge, lease.run_id
    assert bridge is not None and session_id is not None
    if lease.number != 1:
        raise ServiceError("conflict", "Start a new connection to continue this realtime Thread")
    await prove(runtime.storage, lease)
    pipe = Pipe(bridge, session_id, side="worker")
    async with AsyncExitStack() as stack:
        async with pipe.handshake():
            offer = await pipe.attach(lease)
            await stack.enter_async_context(pipe.subscribe())
            await pipe.send(b"a" + lease.attempt_id.encode() + b"/" + bridge.id.encode())
            start = await pipe.receive()
        if not start.startswith(b"o"):
            raise unavailable()
        channel = Channel(
            offer.principal_id,
            offer.workspace_id,
            audio=offer.audio,
            webrtc_offer=start[1:].decode() or None,
            transcript_window=runtime.settings.worker.stream_coalesce_seconds,
            max_duration_seconds=offer.max_duration_seconds,
        )

        async def verify_dispatch(call_id: str) -> bool:
            if channel.closed.is_set() and not channel.failed:
                return False
            await prove(runtime.storage, lease)
            async with short_session(runtime.storage) as session:
                call = await session.get_one(RealtimeCallRow, lease.run_id)
                actor = await reauthenticate(session, runtime.access, _AUTH.validate_python(call.credential))
                await workspace_scope(session, actor, offer.workspace_id, "run")
            if channel.failed:
                raise unavailable()
            try:
                closed = await pipe.redis.get(pipe.inbox + ":close")
            except RedisError:
                raise unavailable() from None
            if closed == pipe.fence or channel.closed.is_set():
                return False
            if call_id in channel.dispatched or not channel.decisions.get(call_id, True):
                raise ServiceError("conflict", "This tool call was already decided")
            channel.dispatched.add(call_id)
            channel.foreground.add(call_id)
            channel.touch()
            return True

        channel.verify_dispatch = verify_dispatch
        channel.draft = offer.draft
        async with _worker_io(pipe, channel):
            yield channel


@asynccontextmanager
async def _worker_io(pipe: Pipe, channel: Channel) -> AsyncIterator[None]:

    async def receive() -> None:
        try:
            while True:
                raw = await pipe.receive()
                if raw in {b"closed", b"stop"}:
                    return
                if raw == b"ping" or channel.closed.is_set():
                    continue
                if raw == b"commit":
                    channel.incoming.put_nowait(Media(type="audio_commit"))
                    continue
                if raw in {b"gap", b"reset"}:
                    if raw == b"gap":
                        channel.emit({"type": "input_discontinuity"})
                    channel.incoming.put_nowait(Media(type="input_discontinuity"))
                    continue
                if raw.startswith(b"b"):
                    channel.incoming.put_nowait(raw[1:])
                    continue
                command = _COMMAND.validate_json(raw[1:])
                if isinstance(command, Approval):
                    answer = channel.approvals.get(command.call_id)
                    if answer is not None and not answer.done():
                        channel.touch()
                        answer.set_result(command.approve)
                elif isinstance(command, Media):
                    if command.type == "interrupt":
                        pipe.epoch += 1
                        while not channel.pcm.empty():
                            channel.pcm.get_nowait()
                    channel.incoming.put_nowait(command)
                elif command.text.strip():
                    channel.touch()
                    channel.incoming.put_nowait(command.text)
        except asyncio.QueueFull:
            channel._overflow()
        except (ServiceError, ValidationError):
            channel.failed = True
        finally:
            channel.closed.set()

    async def send() -> None:
        try:
            while True:
                frame = await channel.outgoing.get()
                await pipe.send(b"j" + _OUTPUT.dump_json(frame))
                if frame.get("type") == "sealed":
                    await pipe.send(b"finish")
                    return
        except ServiceError:
            channel.failed = True
            channel.closed.set()

    async def media() -> None:
        try:
            while True:
                epoch, frame = await channel.pcm.get()
                if epoch == pipe.epoch:
                    await pipe.send(b"b" + frame)
        except ServiceError:
            channel.failed = True
            channel.closed.set()

    async def heartbeat() -> None:
        try:
            while True:
                await pipe.send(b"ping")
                await asyncio.sleep(HEARTBEAT_SECONDS)
        except ServiceError:
            channel.failed = True
            channel.closed.set()

    reader = asyncio.create_task(receive())
    writer = asyncio.create_task(send())
    workers = (reader, writer, asyncio.create_task(media()), asyncio.create_task(heartbeat()))
    try:
        yield
        if channel.finished.is_set():
            await asyncio.wait((writer,), timeout=2 * HEARTBEAT_SECONDS)
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if not channel.finished.is_set():
            await pipe.shutdown()
