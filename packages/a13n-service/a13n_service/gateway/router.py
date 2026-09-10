"""Native Protocol Gateway HTTP and WebSocket routes."""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Literal, cast

import anyio
from ag_ui.core import RunAgentInput
from fastapi import APIRouter, Depends, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.ids import new_object_id
from a13n_service.interactions.acceptance import RunAcceptanceReceipt
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.control_domain import (
    ConsumeQueuedSubmissionRequest,
    InterruptReceipt,
    InterruptRequest,
    QueuedSubmission,
    QueuedSubmissionCollection,
    QueuedSubmissionConsumptionReceipt,
    QueuedSubmissionMutationReceipt,
    QueuedSubmissionState,
    ReorderQueuedSubmissionsRequest,
    SteerReceipt,
    SteerStatus,
    ThreadQueueMutationReceipt,
    ThreadRunSubmissionReceipt,
    ThreadRunSubmissionRequest,
    UpdateQueuedSubmissionRequest,
    WaitingRunFeedbackRequest,
)
from a13n_service.interactions.input import AgentInput
from a13n_service.interactions.submissions import DeleteQueuedSubmissionRequest, QueuedSubmissionService
from a13n_service.process.runtime import ProcessRuntime
from a13n_service.request_runtime import get_control_runtime

from .hosted_agui import HostedAguiCancelReceipt, HostedAguiCancelRequest, HostedAguiService
from .native_streaming import NativeRunStreamService
from .notifications import (
    AuthorizedNotificationSubscription,
    NotificationError,
    NotificationFact,
    NotificationService,
    NotificationSubscription,
    NotificationTopic,
)
from .queries import (
    ItemCollection,
    NativeInteractionQueries,
    PendingActionCollection,
    RunAttemptCollection,
    RunAttemptResource,
    RunCollection,
    RunLineage,
    RunResource,
    SessionCollection,
    ThreadCollection,
    ThreadResource,
)
from .requests import ContinueRunRequest, ForkRunRequest, RetryRunRequest, StartRunRequest

NOTIFICATION_SUBPROTOCOL = "a13n.service.notifications.v1"

router = APIRouter(tags=["protocol-gateway"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


class SubscribeFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["subscribe"]
    request_id: str = Field(min_length=1, max_length=128)
    subscriptions: tuple[NotificationSubscription, ...]


class UnsubscribeFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["unsubscribe"]
    request_id: str = Field(min_length=1, max_length=128)
    subscription_ids: tuple[str, ...]


class HeartbeatAckFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["heartbeat_ack"]
    nonce: str = Field(min_length=1, max_length=128)


ClientFrame = SubscribeFrame | UnsubscribeFrame | HeartbeatAckFrame
_CLIENT_FRAME = TypeAdapter(ClientFrame)


def _native_streams(request: Request) -> NativeRunStreamService:
    control = get_control_runtime(request)
    if control is None:
        raise ApplicationError(
            "gateway_unavailable",
            "The Protocol Gateway is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.gateway.native_streams


def _queued_submissions(request: Request) -> QueuedSubmissionService:
    control = get_control_runtime(request)
    if control is None:
        raise ApplicationError(
            "gateway_unavailable",
            "The a13n Service Gateway is unavailable in this process role.",
            category=ErrorCategory.unavailable,
        )
    return control.gateway.queued_submissions


def _queries(request: Request) -> NativeInteractionQueries:
    control = get_control_runtime(request)
    if control is None:
        raise ApplicationError(
            "gateway_unavailable",
            "The Protocol Gateway is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.gateway.queries


def _commands(request: Request) -> InteractionCommands:
    control = get_control_runtime(request)
    if control is None:
        raise ApplicationError(
            "gateway_unavailable",
            "The Protocol Gateway is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.gateway.commands


def _hosted_agui(request: Request) -> HostedAguiService:
    control = get_control_runtime(request)
    if control is None:
        raise ApplicationError(
            "gateway_unavailable",
            "The Protocol Gateway is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.gateway.hosted_agui


@router.post("/ag-ui/v1/agents/{agent_id}/runs", response_class=StreamingResponse)
async def hosted_agui_run(
    request: Request,
    actor: Actor,
    agent_id: str,
    body: RunAgentInput,
    accept: Annotated[str | None, Header()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=128)] = None,
) -> StreamingResponse:
    media_types = {item.partition(";")[0].strip().lower() for item in (accept or "").split(",")}
    if "text/event-stream" not in media_types:
        raise ApplicationError(
            "not_acceptable",
            "Accept must include text/event-stream.",
            category=ErrorCategory.not_acceptable,
        )
    service = _hosted_agui(request)
    attachment = await service.accept(
        actor=actor,
        agent_id=agent_id,
        request=body,
        last_event_id=last_event_id,
    )
    return StreamingResponse(
        service.events(attachment),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/ag-ui/v1/agents/{agent_id}/cancel",
    response_model=HostedAguiCancelReceipt,
    status_code=202,
)
async def cancel_hosted_agui_run(
    request: Request,
    actor: Actor,
    agent_id: str,
    body: HostedAguiCancelRequest,
) -> HostedAguiCancelReceipt:
    return await _hosted_agui(request).cancel(actor=actor, agent_id=agent_id, request=body)


@router.post(
    "/api/v1/workspaces/{workspace}/runs",
    response_model=RunAcceptanceReceipt,
    status_code=202,
)
async def start_run(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: StartRunRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> RunAcceptanceReceipt:
    return await _commands(request).runs.start(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body.to_command(),
    )


@router.post(
    "/api/v1/runs/{source_run_id}/continue",
    response_model=RunAcceptanceReceipt,
    status_code=202,
)
async def continue_from_run(
    request: Request,
    actor: Actor,
    source_run_id: str,
    body: ContinueRunRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> RunAcceptanceReceipt:
    return await _commands(request).runs.continue_from(
        actor=actor,
        source_run_id=source_run_id,
        idempotency_key=idempotency_key,
        request=body.to_command(),
    )


@router.post(
    "/api/v1/runs/{run_id}/fork",
    response_model=RunAcceptanceReceipt,
    status_code=202,
)
async def fork_run(
    request: Request,
    actor: Actor,
    run_id: str,
    body: ForkRunRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> RunAcceptanceReceipt:
    return await _commands(request).runs.fork(
        actor=actor,
        run_id=run_id,
        idempotency_key=idempotency_key,
        request=body.to_command(),
    )


@router.post(
    "/api/v1/runs/{run_id}/retry",
    response_model=RunAcceptanceReceipt,
    status_code=202,
)
async def retry_run(
    request: Request,
    actor: Actor,
    run_id: str,
    body: RetryRunRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> RunAcceptanceReceipt:
    return await _commands(request).continuations.retry(
        actor=actor,
        run_id=run_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.post(
    "/api/v1/runs/{run_id}/feedback",
    response_model=RunAcceptanceReceipt,
    status_code=202,
)
async def feedback_run(
    request: Request,
    actor: Actor,
    run_id: str,
    body: WaitingRunFeedbackRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> RunAcceptanceReceipt:
    return await _commands(request).continuations.feedback(
        actor=actor,
        run_id=run_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.get(
    "/api/v1/threads/{thread_id}/queued-submissions",
    response_model=QueuedSubmissionCollection,
)
async def list_queued_submissions(
    request: Request,
    actor: Actor,
    thread_id: str,
    state: Annotated[QueuedSubmissionState, Query()] = QueuedSubmissionState.queued,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> QueuedSubmissionCollection:
    return await _queued_submissions(request).list(
        actor=actor,
        thread_id=thread_id,
        state=state,
        limit=limit,
    )


@router.post(
    "/api/v1/threads/{thread_id}/runs",
    response_model=ThreadRunSubmissionReceipt,
    status_code=202,
)
async def submit_thread_run(
    request: Request,
    actor: Actor,
    thread_id: str,
    body: ThreadRunSubmissionRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> ThreadRunSubmissionReceipt:
    return await _queued_submissions(request).submit(
        actor=actor,
        thread_id=thread_id,
        request=body,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/api/v1/threads/{thread_id}/queued-submissions/consume",
    response_model=QueuedSubmissionConsumptionReceipt,
    status_code=202,
)
async def consume_queued_submission(
    request: Request,
    actor: Actor,
    thread_id: str,
    body: ConsumeQueuedSubmissionRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> QueuedSubmissionConsumptionReceipt:
    return await _queued_submissions(request).consume(
        actor=actor,
        thread_id=thread_id,
        request=body,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/api/v1/queued-submissions/{queued_submission_id}",
    response_model=QueuedSubmission,
)
async def get_queued_submission(
    request: Request,
    actor: Actor,
    queued_submission_id: str,
) -> QueuedSubmission:
    return await _queued_submissions(request).get(
        actor=actor,
        queued_submission_id=queued_submission_id,
    )


@router.patch(
    "/api/v1/queued-submissions/{queued_submission_id}",
    response_model=QueuedSubmissionMutationReceipt,
)
async def update_queued_submission(
    request: Request,
    actor: Actor,
    queued_submission_id: str,
    body: UpdateQueuedSubmissionRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> QueuedSubmissionMutationReceipt:
    return await _queued_submissions(request).update(
        actor=actor,
        queued_submission_id=queued_submission_id,
        request=body,
        idempotency_key=idempotency_key,
    )


@router.delete(
    "/api/v1/queued-submissions/{queued_submission_id}",
    response_model=ThreadQueueMutationReceipt,
)
async def delete_queued_submission(
    request: Request,
    actor: Actor,
    queued_submission_id: str,
    body: DeleteQueuedSubmissionRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> ThreadQueueMutationReceipt:
    return await _queued_submissions(request).delete(
        actor=actor,
        queued_submission_id=queued_submission_id,
        request=body,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/api/v1/threads/{thread_id}/queued-submissions/reorder",
    response_model=ThreadQueueMutationReceipt,
)
async def reorder_queued_submissions(
    request: Request,
    actor: Actor,
    thread_id: str,
    body: ReorderQueuedSubmissionsRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> ThreadQueueMutationReceipt:
    return await _queued_submissions(request).reorder(
        actor=actor,
        thread_id=thread_id,
        request=body,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/api/v1/runs/{run_id}/interrupt",
    response_model=InterruptReceipt,
    status_code=202,
)
async def interrupt_run(
    request: Request,
    actor: Actor,
    run_id: str,
    body: InterruptRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> InterruptReceipt:
    return await _commands(request).active.interrupt(
        actor=actor,
        run_id=run_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.post(
    "/api/v1/runs/{run_id}/steer",
    response_model=SteerReceipt,
    status_code=202,
)
async def steer_run(
    request: Request,
    actor: Actor,
    run_id: str,
    body: AgentInput,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=512)],
) -> SteerReceipt:
    return await _commands(request).active.steer(
        actor=actor,
        run_id=run_id,
        idempotency_key=idempotency_key,
        input=body,
    )


@router.get("/api/v1/runs/{run_id}/steers/{steer_id}", response_model=SteerStatus)
async def get_run_steer(
    request: Request,
    actor: Actor,
    run_id: str,
    steer_id: str,
) -> SteerStatus:
    return await _commands(request).active.get_steer(actor=actor, run_id=run_id, steer_id=steer_id)


@router.get("/api/v1/workspaces/{workspace}/sessions", response_model=SessionCollection)
async def list_sessions(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> SessionCollection:
    return await _queries(request).list_sessions(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/threads/{thread_id}", response_model=ThreadResource)
async def get_thread(request: Request, actor: Actor, thread_id: str) -> ThreadResource:
    return await _queries(request).get_thread(actor=actor, thread_id=thread_id)


@router.get("/api/v1/sessions/{session_id}/threads", response_model=ThreadCollection)
async def list_threads(
    request: Request,
    actor: Actor,
    session_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ThreadCollection:
    return await _queries(request).list_threads(actor=actor, session_id=session_id, limit=limit, cursor=cursor)


@router.get("/api/v1/runs/{run_id}", response_model=RunResource)
async def get_run(request: Request, actor: Actor, run_id: str) -> RunResource:
    return await _queries(request).get_run(actor=actor, run_id=run_id)


@router.get("/api/v1/workspaces/{workspace}/runs", response_model=RunCollection)
async def list_workspace_runs(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> RunCollection:
    return await _queries(request).list_runs(
        actor=actor,
        workspace_id=workspace_id,
        thread_id=None,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/threads/{thread_id}/runs", response_model=RunCollection)
async def list_thread_runs(
    request: Request,
    actor: Actor,
    thread_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> RunCollection:
    return await _queries(request).list_runs(
        actor=actor,
        workspace_id=None,
        thread_id=thread_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/runs/{run_id}/attempts", response_model=RunAttemptCollection)
async def list_run_attempts(
    request: Request,
    actor: Actor,
    run_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> RunAttemptCollection:
    return await _queries(request).list_attempts(actor=actor, run_id=run_id, limit=limit, cursor=cursor)


@router.get("/api/v1/run-attempts/{run_attempt_id}", response_model=RunAttemptResource)
async def get_run_attempt(request: Request, actor: Actor, run_attempt_id: str) -> RunAttemptResource:
    return await _queries(request).get_attempt(actor=actor, run_attempt_id=run_attempt_id)


@router.get("/api/v1/runs/{run_id}/pending-actions", response_model=PendingActionCollection)
async def list_pending_actions(request: Request, actor: Actor, run_id: str) -> PendingActionCollection:
    return await _queries(request).pending_actions(actor=actor, run_id=run_id)


@router.get("/api/v1/runs/{run_id}/items", response_model=ItemCollection)
async def list_run_items(
    request: Request,
    actor: Actor,
    run_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ItemCollection:
    return await _queries(request).items(actor=actor, run_id=run_id, limit=limit, cursor=cursor)


@router.get("/api/v1/runs/{run_id}/lineage", response_model=RunLineage)
async def get_run_lineage(request: Request, actor: Actor, run_id: str) -> RunLineage:
    return await _queries(request).lineage(actor=actor, run_id=run_id)


@router.get(
    "/api/v1/runs/{run_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream_run(
    request: Request,
    actor: Actor,
    run_id: str,
    accept: Annotated[str | None, Header()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=128)] = None,
) -> StreamingResponse:
    media_types = {item.partition(";")[0].strip().lower() for item in (accept or "").split(",")}
    if "text/event-stream" not in media_types:
        raise ApplicationError(
            "not_acceptable",
            "Accept must include text/event-stream.",
            category=ErrorCategory.not_acceptable,
        )
    service = _native_streams(request)
    attachment = await service.attach(actor=actor, run_id=run_id, after_stream_id=last_event_id)
    return StreamingResponse(
        service.events(attachment),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/api/v1/notifications")
async def notifications(websocket: WebSocket) -> None:
    runtime = getattr(websocket.app.state, "runtime", None)
    if not isinstance(runtime, ProcessRuntime) or runtime.control is None:
        await _deny_websocket(websocket, 503, "gateway_unavailable", "The Protocol Gateway is unavailable.")
        return
    if NOTIFICATION_SUBPROTOCOL not in websocket.scope.get("subprotocols", ()):
        await _deny_websocket(websocket, 400, "unsupported_subprotocol", "The notification subprotocol is required.")
        return
    authenticator = runtime.request_authenticator
    if authenticator is None:
        await _deny_websocket(websocket, 401, "authentication_required", "Authentication is required.")
        return
    try:
        actor = await authenticator(cast(Request, websocket))
    except Exception:
        await _deny_websocket(websocket, 401, "authentication_required", "Authentication is required.")
        return

    await websocket.accept(subprotocol=NOTIFICATION_SUBPROTOCOL)
    connection = _NotificationConnection(
        websocket,
        actor=actor,
        service=runtime.control.gateway.notifications,
        runtime=runtime,
    )
    await connection.run()


class _NotificationConnection:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        actor: AuthenticatedActor,
        service: NotificationService,
        runtime: ProcessRuntime,
    ) -> None:
        self._websocket = websocket
        self._actor = actor
        self._service = service
        self._runtime = runtime
        self._settings = runtime.settings
        self._subscriptions: dict[str, AuthorizedNotificationSubscription] = {}
        self._state_lock = anyio.Lock()
        self._send_lock = anyio.Lock()
        self._heartbeat_nonce: str | None = None
        self._heartbeat_acknowledged = True
        self._started = monotonic()
        self._last_authorized = self._started

    async def run(self) -> None:
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self._poll)
                tasks.start_soon(self._heartbeat)
                try:
                    await self._receive()
                finally:
                    tasks.cancel_scope.cancel()
        except WebSocketDisconnect:
            return
        except NotificationError as error:
            await self._safe_close(1008, error.code)
        except Exception:
            await self._safe_close(1011, "notification_dependency_failed")

    async def _receive(self) -> None:
        while True:
            raw = await self._websocket.receive_text()
            if len(raw.encode()) > self._settings.gateway.notification_max_frame_bytes:
                await self._safe_close(1009, "frame_too_large")
                return
            try:
                frame = _CLIENT_FRAME.validate_json(raw)
            except ValidationError:
                await self._send_error(None, "protocol_error", "The notification frame is invalid.")
                await self._safe_close(1008, "protocol_error")
                return
            if isinstance(frame, SubscribeFrame):
                await self._subscribe(frame)
            elif isinstance(frame, UnsubscribeFrame):
                await self._unsubscribe(frame)
            else:
                if frame.nonce != self._heartbeat_nonce:
                    await self._safe_close(1008, "heartbeat_mismatch")
                    return
                self._heartbeat_acknowledged = True

    async def _subscribe(self, frame: SubscribeFrame) -> None:
        async with self._state_lock:
            resulting_count = len(set(self._subscriptions).union(item.subscription_id for item in frame.subscriptions))
            resulting_topics = sum(
                len(item.definition.topics)
                for key, item in self._subscriptions.items()
                if key not in {candidate.subscription_id for candidate in frame.subscriptions}
            ) + sum(len(item.topics) for item in frame.subscriptions)
        if resulting_count > self._settings.gateway.notification_max_subscriptions:
            await self._send_error(frame.request_id, "subscription_limit_exceeded", "Too many subscriptions.")
            return
        if resulting_topics > self._settings.gateway.notification_max_topics:
            await self._send_error(frame.request_id, "topic_limit_exceeded", "Too many subscription topics.")
            return
        try:
            authorized = await self._service.authorize(actor=self._actor, subscriptions=frame.subscriptions)
        except NotificationError as error:
            await self._send_error(frame.request_id, error.code, error.message)
            return
        async with self._state_lock:
            updated = dict(self._subscriptions)
            updated.update({item.definition.subscription_id: item for item in authorized})
            self._subscriptions = updated
            resulting_ids = sorted(updated)
        await self._send_json({"type": "subscribed", "request_id": frame.request_id, "subscription_ids": resulting_ids})

    async def _unsubscribe(self, frame: UnsubscribeFrame) -> None:
        async with self._state_lock:
            missing = sorted(set(frame.subscription_ids).difference(self._subscriptions))
            if missing:
                await self._send_error(frame.request_id, "subscription_not_found", "A subscription was not found.")
                return
            updated = dict(self._subscriptions)
            for subscription_id in frame.subscription_ids:
                updated.pop(subscription_id)
            self._subscriptions = updated
            resulting_ids = sorted(updated)
        await self._send_json(
            {"type": "unsubscribed", "request_id": frame.request_id, "subscription_ids": resulting_ids}
        )

    async def _poll(self) -> None:
        while True:
            if self._runtime.status.draining:
                await self._safe_close(1001, "service_draining")
                return
            now = monotonic()
            if now - self._started >= self._settings.gateway.notification_maximum_lifetime_seconds:
                await self._safe_close(1001, "connection_lifetime_reached")
                return
            async with self._state_lock:
                subscriptions = tuple(self._subscriptions.values())
            if now - self._last_authorized >= self._settings.gateway.stream_authorization_interval_seconds:
                subscriptions = tuple(
                    [await self._service.reauthorize(actor=self._actor, subscription=item) for item in subscriptions]
                )
                async with self._state_lock:
                    for item in subscriptions:
                        if item.definition.subscription_id in self._subscriptions:
                            self._subscriptions[item.definition.subscription_id] = item
                self._last_authorized = now
            for subscription in subscriptions:
                facts = await self._service.read(
                    subscription,
                    limit=self._settings.gateway.notification_poll_limit,
                )
                for fact in facts:
                    for topic in _fact_topics(fact, subscription.definition.topics):
                        await self._send_notification(subscription, fact, topic)
                if facts:
                    async with self._state_lock:
                        current = self._subscriptions.get(subscription.definition.subscription_id)
                        if current is not None:
                            self._subscriptions[subscription.definition.subscription_id] = replace(
                                current,
                                after_seq=facts[-1].seq,
                            )
            await anyio.sleep(self._settings.gateway.notification_poll_interval_seconds)

    async def _heartbeat(self) -> None:
        interval = self._settings.gateway.notification_heartbeat_interval_seconds
        while True:
            await anyio.sleep(interval)
            if not self._heartbeat_acknowledged:
                await self._safe_close(1008, "heartbeat_timeout")
                return
            nonce = secrets.token_urlsafe(18)
            self._heartbeat_nonce = nonce
            self._heartbeat_acknowledged = False
            await self._send_json(
                {
                    "type": "heartbeat",
                    "nonce": nonce,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "ack_interval_seconds": interval,
                }
            )

    async def _send_notification(
        self,
        subscription: AuthorizedNotificationSubscription,
        fact: NotificationFact,
        topic: NotificationTopic,
    ) -> None:
        await self._send_json(
            {
                "type": "notification",
                "schema_version": "1",
                "notification_id": new_object_id("ntf"),
                "subscription_id": subscription.definition.subscription_id,
                "topic": topic,
                "workspace_id": fact.workspace_id,
                "resource_type": fact.resource_type,
                "resource_id": fact.resource_id,
                "resource_version": fact.resource_version,
                "session_id": fact.session_id,
                "thread_id": fact.thread_id,
                "run_id": fact.run_id,
                "occurred_at": _utc(fact.occurred_at).isoformat(),
            }
        )

    async def _send_error(self, request_id: str | None, code: str, message: str) -> None:
        await self._send_json({"type": "error", "request_id": request_id, "code": code, "message": message})

    async def _send_json(self, value: dict[str, object]) -> None:
        async with self._send_lock:
            with anyio.fail_after(self._settings.gateway.notification_send_timeout_seconds):
                await self._websocket.send_json(value)

    async def _safe_close(self, code: int, reason: str) -> None:
        try:
            await self._websocket.close(code=code, reason=reason[:123])
        except RuntimeError:
            pass


def _fact_topics(fact: NotificationFact, selected: tuple[NotificationTopic, ...]) -> tuple[NotificationTopic, ...]:
    candidates: list[NotificationTopic] = ["run.updated", "thread.updated", "session.updated"]
    if fact.event_type == "run.waiting":
        candidates.append("pending_action.updated")
    return tuple(topic for topic in candidates if topic in selected)


async def _deny_websocket(websocket: WebSocket, status_code: int, code: str, message: str) -> None:
    await websocket.send_denial_response(
        JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, "details": {}}})
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = ["NOTIFICATION_SUBPROTOCOL", "router"]
