"""Same-origin HTTP delivery over one in-memory WebUI App lifetime."""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import json
import os
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit

import click
import uvicorn
from anyio import CancelScope, Event, Lock, create_task_group, fail_after, move_on_after, sleep
from anyio.abc import TaskStatus
from fastapi import FastAPI, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError, model_validator
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from a13n_harness_ui import __version__
from a13n_harness_ui.app import AppState, AppStatus, HarnessUiApp
from a13n_harness_ui.configuration import ResourceMutationRequest
from a13n_harness_ui.configuration.setup import SetupPreview, SetupPublication, SetupSelection
from a13n_harness_ui.configuration.views import (
    AgentToolProxyView,
    ConfigurationPublication,
    ConfigurationSourceCatalog,
    ConfigurationSourceView,
    ConfigurationValidation,
)
from a13n_harness_ui.configuration_inspection import CapturedConfiguration, ThreadConfigurationInspection
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.extensions import CatalogReference
from a13n_harness_ui.host_files import (
    DirectoryCreateRequest,
    DirectoryPage,
    FileCapture,
    FileCaptureRequest,
    FileDeleteRequest,
    FileDeletion,
    FileEntry,
    FileMoveRequest,
    FileReadRequest,
    FileText,
    FileWriteRequest,
    NativePath,
    Revision,
)
from a13n_harness_ui.host_git import (
    Comparison,
    GitCaptureRequest,
    GitDiff,
    GitDiffRequest,
    GitDiscovery,
    GitPath,
    GitStatus,
)
from a13n_harness_ui.host_terminal import TerminalCommand, TerminalCreate, TerminalFrame, TerminalView
from a13n_harness_ui.interactive_transport import InteractiveAuthentication, authenticate_interactive, receive_text
from a13n_harness_ui.live import LiveCursor, LiveEvent, RootStreamEvent, SummaryCursor, SummaryInvalidation
from a13n_harness_ui.model_accounts import AccountProjection, AccountStoreError, Provider
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStatus
from a13n_harness_ui.model_accounts.login import LoginRequest, LoginStatus
from a13n_harness_ui.model_authoring import (
    ModelChoices,
    ModelOptions,
    ModelOptionsRequest,
    ModelRecipe,
    ModelRecipeRequest,
)
from a13n_harness_ui.model_catalog import ModelCatalogSnapshot
from a13n_harness_ui.model_thinking import ThinkingSelection
from a13n_harness_ui.output_comment_models import (
    CommentEdit,
    CommentPage,
    CommentPublication,
    OutputComment,
    SavedChildOutputPage,
    SavedOutputTarget,
    SavedOutputView,
)
from a13n_harness_ui.page_presence import (
    PRESENCE_REFRESH_SECONDS,
    PRESENCE_TIMEOUT_SECONDS,
    PointerFrame,
    PointerReport,
    PresenceFrame,
    PresenceReport,
)
from a13n_harness_ui.push_models import PushConfiguration, PushSubscriptionInput, PushSubscriptionView, PushTestResult
from a13n_harness_ui.setup import EnvironmentReadiness, SetupStatus
from a13n_harness_ui.shared_drafts import DraftCommand, DraftFrame
from a13n_harness_ui.storage import ThreadConfiguration
from a13n_harness_ui.storage.usage import ThreadUsageView
from a13n_harness_ui.surfaces import (
    ChildControlResult,
    ChildExecutionPage,
    ContextUsageView,
    DecisionBatchView,
    DecisionResponseBatch,
    NewThreadDefaults,
    NotePage,
    ProjectDefaultsApply,
    ProjectDefaultsPreview,
    ProjectSummary,
    ReviewView,
    RootControlResult,
    RootOperationView,
    RootRunReceipt,
    RunModelOverrides,
    SkillCatalogView,
    SkillReference,
    SurfaceModel,
    TaskPage,
    ThreadActivityPage,
    ThreadActivityView,
    ThreadConfigurationMutationInput,
    ThreadConfigurationResolution,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadLookup,
    ThreadMetadataMutation,
    ThreadPage,
    ThreadSelectorCatalog,
    ThreadSummary,
    TranscriptInputPage,
    TranscriptPage,
)
from a13n_harness_ui.thread_files import (
    MAX_ATTACHMENT_BYTES,
    AttachmentUpload,
    ComposerAttachmentReference,
    ComposerInput,
    ThreadAttachment,
)
from a13n_harness_ui.webui_lifecycle import ErrorResponse, RequestLog, WebUIServer

API_VERSION = "1"
_MAX_BODY = 1024 * 1024
_STATIC = Path(__file__).parent / "static"
AppFactory = Callable[[], AbstractAsyncContextManager[HarnessUiApp]]


class ListenerFeatures(SurfaceModel):
    """Implemented browser facilities, not the eventual workbench roadmap."""

    shared_drafts: Literal[True] = True
    output_comments: Literal[True] = True
    page_presence: Literal[True] = True
    host_files: bool = False
    host_git: bool = False
    host_terminal: bool = False


class ListenerStatus(SurfaceModel):
    api_version: Literal["1"] = "1"
    version: str
    build_revision: str | None = None
    features: ListenerFeatures = Field(default_factory=ListenerFeatures)
    app: AppStatus
    host: str
    access: Literal["api_key", "dangerous_bypass"]


class CreateThreadRequest(SurfaceModel):
    thread_id: str | None = Field(default=None, pattern=r"^thread[-_][0-9a-f]{32}$")
    defaults: NewThreadDefaults | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)


class InputAttachmentReference(SurfaceModel):
    attachment_id: str = Field(min_length=1, max_length=100)


class PromptRequest(SurfaceModel):
    prompt: str = Field(default="", max_length=256 * 1024)
    attachment_ids: tuple[str, ...] = Field(default=(), max_length=8)
    parts: tuple[str | InputAttachmentReference, ...] | None = Field(default=None, max_length=1024)
    skill_references: tuple[SkillReference, ...] = Field(default=(), max_length=512)
    # Presentation correlation only; never an admission idempotency key.
    source_id: str | None = Field(default=None, pattern=r"^input[-_][0-9a-f]{32}$")

    @model_validator(mode="after")
    def validate_ordered_input(self) -> PromptRequest:
        if self.source_id is not None and self.parts is None:
            raise ValueError("source_id requires ordered parts.")
        if self.parts is not None:
            if self.prompt or self.attachment_ids:
                raise ValueError("Use ordered parts or prompt/attachment_ids, not both.")
            if sum(len(part) for part in self.parts if isinstance(part, str)) > 256 * 1024:
                raise ValueError("Authored input exceeds 256 Ki characters.")
        return self

    def input(self) -> str | ComposerInput:
        if self.parts is None:
            return self.prompt
        return ComposerInput(
            parts=tuple(
                part if isinstance(part, str) else ComposerAttachmentReference(part.attachment_id)
                for part in self.parts
            ),
            source_id=self.source_id,
        )


class SteerRequest(SurfaceModel):
    prompt: str = Field(min_length=1, max_length=256 * 1024)


class SubmitRequest(PromptRequest):
    environment_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    thinking: ThinkingSelection | None = None
    fast: bool | None = None


class RootSteerRequest(PromptRequest):
    @model_validator(mode="after")
    def validate_instruction(self) -> RootSteerRequest:
        if self.parts is None and not self.prompt and not self.attachment_ids:
            raise ValueError("An instruction must not be empty.")
        return self


class SetupApplyRequest(SurfaceModel):
    selection: SetupSelection


class PreflightRequest(SurfaceModel):
    profile_id: Literal["environment-native", "environment-sandbox"]
    project_path: str = Field(min_length=1, max_length=4096)


class FocusSnapshotFrame(SurfaceModel):
    kind: Literal["snapshot"] = "snapshot"
    snapshot: ThreadFocusSnapshot
    resume_cursor: str | None


class FocusReplayFrame(SurfaceModel):
    kind: Literal["root_stream"] = "root_stream"
    run_id: str
    events: tuple[RootStreamEvent, ...] = Field(min_length=1, max_length=16)


class FocusReadyFrame(SurfaceModel):
    kind: Literal["ready"] = "ready"
    resume_cursor: str


class FocusEventFrame(SurfaceModel):
    kind: Literal["event"] = "event"
    event: LiveEvent
    resume_cursor: str


class ResetFrame(SurfaceModel):
    kind: Literal["reset"] = "reset"
    reason: str


class SummaryOpenFrame(SurfaceModel):
    resumed: bool = False
    kind: Literal["open"] = "open"
    cursor: SummaryCursor
    resume_cursor: str


class SummaryEventFrame(SurfaceModel):
    kind: Literal["invalidation"] = "invalidation"
    event: SummaryInvalidation
    resume_cursor: str


class RealtimeCommand(SurfaceModel):
    version: Literal[1] = 1
    kind: Literal["subscribe", "unsubscribe", "pong"]
    channel: str = Field(default="", max_length=80)
    stream: Literal["summary", "focus"] = "summary"
    root_thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    after: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def _scope(self) -> RealtimeCommand:
        if self.kind != "pong" and not self.channel:
            raise ValueError("A channel identity is required")
        if self.kind == "subscribe" and (self.stream == "focus") != (self.root_thread_id is not None):
            raise ValueError("Only focus subscriptions require a root identity")
        return self


class RealtimeFrame(SurfaceModel):
    version: Literal[1] = 1
    channel: str
    frame: (
        FocusSnapshotFrame
        | FocusReplayFrame
        | FocusReadyFrame
        | FocusEventFrame
        | SummaryOpenFrame
        | SummaryEventFrame
        | ResetFrame
    ) = Field(discriminator="kind")


class RealtimePing(SurfaceModel):
    version: Literal[1] = 1
    kind: Literal["ping"] = "ping"


class StreamCursor(SurfaceModel):
    kind: Literal["focus", "summary"]
    root_thread_id: str | None
    epoch: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=0)


def _cursor(kind: Literal["focus", "summary"], root: str | None, epoch: str, sequence: int) -> str:
    return base64.urlsafe_b64encode(
        StreamCursor(kind=kind, root_thread_id=root, epoch=epoch, sequence=sequence).model_dump_json().encode()
    ).decode()


def _parse_cursor(value: str, kind: Literal["focus", "summary"], root: str | None) -> StreamCursor:
    try:
        parsed = StreamCursor.model_validate_json(base64.b64decode(value, altchars=b"-_", validate=True))
        if parsed.kind != kind or parsed.root_thread_id != root:
            raise ValueError("Cursor scope differs")
        return parsed
    except (ValidationError, ValueError, binascii.Error):
        raise HarnessUiError(
            "The stream cursor does not match this subscription.", code="stream_cursor_invalid"
        ) from None


class ErrorBody(SurfaceModel):
    code: str
    message: str


class ErrorEnvelope(SurfaceModel):
    error: ErrorBody


def _error(code: str, message: str, status: int) -> JSONResponse:
    return ErrorResponse(code=code, message=message, status_code=status)


class AccessBoundary:
    """Authenticate before body parsing or App access, without logging secrets."""

    def __init__(self, app: ASGIApp, *, api_key: str | None, allowed_hosts: frozenset[str]) -> None:
        self.app = app
        self.api_key = api_key
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        request = HTTPConnection(scope)
        interactive = scope["type"] == "websocket"

        async def reject(code: str, message: str, status: int) -> None:
            if interactive:
                await send({"type": "websocket.close", "code": 4403, "reason": message})
            else:
                await _error(code, message, status)(scope, receive, send)

        host = request.url.hostname
        wildcard_ip = False
        if {"0.0.0.0", "::"} & self.allowed_hosts and host is not None:
            try:
                ipaddress.ip_address(host)
                wildcard_ip = True
            except ValueError:
                pass
        if host not in self.allowed_hosts and not wildcard_ip:
            await reject("host_rejected", "Use the listener's explicit browser address.", 400)
            return
        if scope["path"] == "/api" or scope["path"].startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme != {"ws": "http", "wss": "https"}.get(request.url.scheme, request.url.scheme)
                    or parsed.netloc != request.headers.get("host")
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                ):
                    await reject("origin_rejected", "Cross-origin API access is not enabled.", 403)
                    return
            authorization = request.headers.get("authorization", "")
            if (
                not interactive
                and self.api_key is not None
                and not hmac.compare_digest(authorization.encode(), f"Bearer {self.api_key}".encode())
            ):
                await _error("authentication_required", "Enter the API key printed by this server.", 401)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


async def _document[T: BaseModel](request: Request, model: type[T]) -> T:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > _MAX_BODY:
            raise HarnessUiError("Request exceeds the one MiB limit.", code="request_too_large")
    try:
        return model.model_validate_json(data)
    except ValidationError:
        # Validation diagnostics can contain the original input; never echo it.
        raise HarnessUiError("Request does not match the API schema.", code="request_invalid") from None


def _body(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": model.model_json_schema(ref_template="#/components/schemas/{model}")}
            },
        }
    }


def create_webui(
    app_factory: AppFactory,
    *,
    api_key: str | None,
    host: str = "127.0.0.1",
    static_root: Path = _STATIC,
    stopping: Event | None = None,
) -> FastAPI:
    """Build the adapter; only its ASGI lifespan opens and owns the App."""
    if api_key == "":
        raise ValueError("API key cannot be empty")
    owner: HarnessUiApp | None = None
    stopping = stopping if stopping is not None else Event()

    @asynccontextmanager
    async def lifespan(_server: FastAPI) -> AsyncIterator[None]:
        nonlocal owner
        async with app_factory() as opened:
            owner = opened
            try:
                yield
            finally:
                owner = None

    def app() -> HarnessUiApp:
        if owner is None:
            raise HarnessUiError("The WebUI server is not ready.", code="app_not_ready")
        return owner

    server = FastAPI(
        title="Harness UI", version=API_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    ipaddress.ip_address(host)
    # Wildcard binds accept IP literals, never arbitrary DNS names.
    hosts = frozenset({host, "localhost", "127.0.0.1", "::1"})
    server.add_middleware(AccessBoundary, api_key=api_key, allowed_hosts=hosts)
    server.add_middleware(RequestLog)

    @server.middleware("http")
    async def response_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @server.exception_handler(HarnessUiError)
    async def app_error(_request: Request, exc: HarnessUiError) -> JSONResponse:
        code = exc.code
        status = 409 if "conflict" in code or "stale" in code or "preflight_required" in code else 400
        if code in {"request_too_large", "host_files_too_large", "host_git_too_large"}:
            status = 413
        elif code in {
            "host_files_disabled",
            "host_files_permission_denied",
            "host_git_disabled",
            "host_terminal_disabled",
            "host_git_permission_denied",
        }:
            status = 403
        elif code in {"host_files_partial_failure", "thread_run_active", "thread_exists", "thread_interaction_expired"}:
            status = 409
        elif code == "host_files_io_error":
            status = 500
        elif code in {"app_not_ready", "app_stopping", "host_git_unavailable", "host_terminal_unavailable"}:
            status = 503
        elif code == "host_git_timeout":
            status = 504
        elif code.endswith("not_found"):
            status = 404
        return _error(code, str(exc), status)

    @server.exception_handler(AccountStoreError)
    async def account_error(_request: Request, exc: AccountStoreError) -> JSONResponse:
        return _error(exc.code, str(exc), 400)

    @server.exception_handler(RequestValidationError)
    async def query_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error("request_invalid", "Query does not match the API schema.", 422)

    @server.get("/healthz", include_in_schema=False)
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    @server.get("/readyz", include_in_schema=False)
    async def ready() -> JSONResponse:
        # Missing model configuration does not block setup or App access.
        available = owner is not None and owner.state is AppState.ready
        return JSONResponse(
            {"status": "ready" if available else "not_ready"},
            status_code=200 if available else 503,
            headers={"Cache-Control": "no-store"},
        )

    @server.get("/api/push/configuration", response_model=PushConfiguration)
    async def push_configuration() -> PushConfiguration:
        return await app().push_configuration()

    @server.put(
        "/api/push/subscription", response_model=PushSubscriptionView, openapi_extra=_body(PushSubscriptionInput)
    )
    async def subscribe_push(request: Request) -> PushSubscriptionView:
        return await app().subscribe_push(await _document(request, PushSubscriptionInput))

    @server.delete("/api/push/subscriptions/{subscription_id}", status_code=204)
    async def unsubscribe_push(subscription_id: str) -> Response:
        await app().unsubscribe_push(subscription_id)
        return Response(status_code=204)

    @server.post("/api/push/subscriptions/{subscription_id}/test", response_model=PushTestResult)
    async def test_push(subscription_id: str) -> PushTestResult:
        return await app().test_push(subscription_id)

    @server.get("/api/status", response_model=ListenerStatus)
    async def status() -> ListenerStatus:
        return ListenerStatus(
            version=__version__,
            build_revision=os.environ.get("A13N_HARNESS_UI_BUILD_REVISION"),
            app=await app().status(),
            features=ListenerFeatures(
                host_files=app().shares_computer,
                host_git=app().host_git_available,
                host_terminal=app().host_terminal_available,
            ),
            host=host,
            access="api_key" if api_key is not None else "dangerous_bypass",
        )

    @server.get("/api/presence", response_model=PresenceFrame)
    async def presence(participant_id: Annotated[str | None, Query(max_length=80)] = None) -> PresenceFrame:
        return await app().page_presence_snapshot(participant_id)

    @server.websocket("/api/presence/connect")
    async def connect_presence(socket: WebSocket) -> None:
        if not await authenticate_interactive(socket, api_key):
            return
        try:
            directory = app().page_presence()
            participant = directory.attach()
        except HarnessUiError as exc:
            await socket.send_json(ErrorEnvelope(error=ErrorBody(code=exc.code, message=str(exc))).model_dump())
            await socket.close(code=4404)
            return
        try:
            async with create_task_group() as group:

                async def output() -> None:
                    while True:
                        changed = directory.changed
                        if directory.closed:
                            frame = PresenceFrame(participant_id=participant, participants=(), closed=True)
                        else:
                            frame = await app().page_presence_snapshot(participant)
                        await socket.send_json(frame.model_dump(mode="json"))
                        if frame.closed:
                            await socket.close()
                            group.cancel_scope.cancel()
                            return
                        with move_on_after(PRESENCE_REFRESH_SECONDS):
                            await changed.wait()

                async def pointer_output() -> None:
                    previous: PointerFrame | None = None
                    while not directory.closed:
                        changed = directory.pointer_changed
                        own = directory.participants.get(participant)
                        if own is not None and own.pointer_enabled:
                            frame = directory.pointer_snapshot(participant)
                            if frame != previous:
                                await socket.send_json(frame.model_dump(mode="json"))
                                previous = frame
                        with move_on_after(1):
                            await changed.wait()
                        # Coalesce latest positions, without a queue or resource I/O.
                        await sleep(0.05)

                group.start_soon(output)
                group.start_soon(pointer_output)
                try:
                    while True:
                        try:
                            with fail_after(PRESENCE_TIMEOUT_SECONDS):
                                raw = await receive_text(socket, limit=16384)
                            payload = json.loads(raw)
                            if isinstance(payload, dict) and payload.get("kind") == "pointer":
                                directory.report_pointer(participant, PointerReport.model_validate(payload))
                            else:
                                report = PresenceReport.model_validate(payload)
                                await app().report_page_presence(participant, report)
                        except (ValidationError, ValueError):
                            await socket.send_json(
                                ErrorEnvelope(
                                    error=ErrorBody(code="presence_invalid", message="Invalid page presence report.")
                                ).model_dump()
                            )
                        except HarnessUiError as exc:
                            await socket.send_json(
                                ErrorEnvelope(error=ErrorBody(code=exc.code, message=str(exc))).model_dump()
                            )
                except TimeoutError:
                    await socket.close(code=4408, reason="Presence report timed out")
                except WebSocketDisconnect:
                    pass
                finally:
                    group.cancel_scope.cancel()
        except* WebSocketDisconnect:
            pass
        finally:
            directory.detach(participant)

    @server.websocket("/api/threads/{thread_id}/draft/connect")
    async def connect_draft(socket: WebSocket, thread_id: str) -> None:
        if not await authenticate_interactive(socket, api_key):
            return
        try:
            draft = await app().shared_draft(thread_id)
        except HarnessUiError as exc:
            await socket.send_json(ErrorEnvelope(error=ErrorBody(code=exc.code, message=str(exc))).model_dump())
            await socket.close(code=4404)
            return
        participant = draft.attach()
        try:
            async with create_task_group() as group:

                async def output() -> None:
                    while True:
                        changed = draft.changed
                        frame = draft.frame(participant)
                        await socket.send_json(frame.model_dump(mode="json"))
                        if frame.closed:
                            await socket.close()
                            group.cancel_scope.cancel()
                            return
                        while not changed.is_set():
                            with move_on_after(1):
                                await changed.wait()
                            draft.expire_presence()

                group.start_soon(output)
                try:
                    while True:
                        try:
                            command = DraftCommand.model_validate_json(await receive_text(socket, limit=710000))
                            await app().edit_shared_draft(thread_id, participant, command)
                        except (ValidationError, ValueError):
                            await socket.send_json(
                                ErrorEnvelope(
                                    error=ErrorBody(
                                        code="draft_invalid",
                                        message="Invalid shared draft; local edits were not accepted.",
                                    )
                                ).model_dump()
                            )
                        except HarnessUiError as exc:
                            await socket.send_json(
                                ErrorEnvelope(error=ErrorBody(code=exc.code, message=str(exc))).model_dump()
                            )
                except WebSocketDisconnect:
                    pass
                finally:
                    group.cancel_scope.cancel()
        except* WebSocketDisconnect:
            pass
        finally:
            draft.detach(participant)

    @server.get("/api/host/terminals", response_model=tuple[TerminalView, ...])
    async def terminals() -> tuple[TerminalView, ...]:
        return await app().list_host_terminals()

    @server.post("/api/host/terminals", response_model=TerminalView, openapi_extra=_body(TerminalCreate))
    async def create_terminal(request: Request) -> TerminalView:
        return await app().create_host_terminal(await _document(request, TerminalCreate))

    @server.get("/api/host/terminals/{terminal_id}", response_model=TerminalView)
    async def terminal(terminal_id: str) -> TerminalView:
        return app().host_terminal(terminal_id).view()

    @server.delete("/api/host/terminals/{terminal_id}", response_model=TerminalView)
    async def close_terminal(terminal_id: str) -> TerminalView:
        return await app().close_host_terminal(terminal_id)

    @server.websocket("/api/host/terminals/{terminal_id}/connect")
    async def connect_terminal(socket: WebSocket, terminal_id: str, cursor: int = 0) -> None:
        if not await authenticate_interactive(socket, api_key):
            return
        try:
            session = app().host_terminal(terminal_id)
        except HarnessUiError as exc:
            await socket.send_json(ErrorEnvelope(error=ErrorBody(code=exc.code, message=str(exc))).model_dump())
            await socket.close(code=4404)
            return
        participant = session.attach()
        try:
            async with create_task_group() as group:

                async def output() -> None:
                    position = cursor
                    while True:
                        changed = session.changed
                        frame = session.frame(participant, position)
                        await socket.send_json(frame.model_dump(mode="json"))
                        position = frame.end
                        if frame.terminal.state == "closed":
                            await socket.close()
                            group.cancel_scope.cancel()
                            return
                        await changed.wait()

                group.start_soon(output)
                try:
                    while True:
                        try:
                            raw = await receive_text(socket, limit=128 * 1024)
                            command = TerminalCommand.model_validate_json(raw)
                            await session.command(participant, command)
                        except (ValidationError, ValueError):
                            await socket.send_json(
                                ErrorEnvelope(
                                    error=ErrorBody(code="request_invalid", message="Invalid terminal command.")
                                ).model_dump()
                            )
                        except HarnessUiError as exc:
                            await socket.send_json(
                                ErrorEnvelope(error=ErrorBody(code=exc.code, message=str(exc))).model_dump()
                            )
                except WebSocketDisconnect:
                    pass
                finally:
                    group.cancel_scope.cancel()
        except* WebSocketDisconnect:
            pass
        finally:
            session.detach(participant)

    @server.get("/api/host/files/metadata", response_model=FileEntry)
    async def host_file_metadata(path: NativePath) -> FileEntry:
        return await app().host_file_metadata(path)

    @server.get("/api/host/files", response_model=DirectoryPage)
    async def host_files(
        path: NativePath,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        revision: Revision | None = None,
    ) -> DirectoryPage:
        return await app().browse_host_files(path, offset=offset, limit=limit, revision=revision)

    @server.get("/api/host/files/text", response_model=FileText)
    async def host_file_text(path: NativePath, expected_revision: Revision | None = None) -> FileText:
        return await app().read_host_file(FileReadRequest(path=path, expected_revision=expected_revision))

    @server.put("/api/host/files/text", response_model=FileEntry, openapi_extra=_body(FileWriteRequest))
    async def save_host_text(request: Request) -> FileEntry:
        app().require_host_files()
        return await app().write_host_file(await _document(request, FileWriteRequest))

    @server.post("/api/host/files/directories", response_model=FileEntry, openapi_extra=_body(DirectoryCreateRequest))
    async def create_host_directory(request: Request) -> FileEntry:
        app().require_host_files()
        return await app().create_host_directory(await _document(request, DirectoryCreateRequest))

    @server.post("/api/host/files/move", response_model=FileEntry, openapi_extra=_body(FileMoveRequest))
    async def move_host_file(request: Request) -> FileEntry:
        app().require_host_files()
        return await app().move_host_file(await _document(request, FileMoveRequest))

    @server.post("/api/host/files/delete", response_model=FileDeletion, openapi_extra=_body(FileDeleteRequest))
    async def delete_host_file(request: Request) -> FileDeletion:
        app().require_host_files()
        return await app().delete_host_file(await _document(request, FileDeleteRequest))

    @server.put(
        "/api/host/files/content",
        response_model=FileEntry,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def upload_host_file(
        request: Request, path: NativePath, expected_revision: Revision | None = None
    ) -> FileEntry:
        app().require_host_files()
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_ATTACHMENT_BYTES:
                raise HarnessUiError("Upload exceeds 10 MiB; no file was written.", code="request_too_large")
            data.extend(chunk)
        return await app().upload_host_file(path, bytes(data), expected_revision=expected_revision)

    @server.get("/api/host/files/content")
    async def download_host_file(path: NativePath, expected_revision: Revision | None = None) -> Response:
        snapshot = await app().download_host_file(FileReadRequest(path=path, expected_revision=expected_revision))
        return Response(
            snapshot.data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(Path(path).name, safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "ETag": f'"{snapshot.entry.revision}"',
            },
        )

    @server.post(
        "/api/threads/{thread_id}/host-file-captures",
        response_model=FileCapture,
        openapi_extra=_body(FileCaptureRequest),
    )
    async def capture_host_file(thread_id: str, request: Request) -> FileCapture:
        app().require_host_files()
        return await app().capture_host_file(thread_id=thread_id, request=await _document(request, FileCaptureRequest))

    @server.get("/api/host/git/repository", response_model=GitDiscovery)
    async def host_repository(path: NativePath) -> GitDiscovery:
        return await app().discover_host_repository(path)

    @server.get("/api/host/git/status", response_model=GitStatus)
    async def host_git_status(
        path: NativePath,
        include_ignored: bool = False,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        expected_revision: Revision | None = None,
    ) -> GitStatus:
        return await app().host_git_status(
            path, include_ignored=include_ignored, offset=offset, limit=limit, expected_revision=expected_revision
        )

    @server.get("/api/host/git/diff", response_model=GitDiff)
    async def host_git_diff(
        repository_path: NativePath,
        path: GitPath,
        comparison: Comparison = "unstaged",
        expected_revision: Revision | None = None,
    ) -> GitDiff:
        return await app().read_host_git_diff(
            GitDiffRequest(
                repository_path=repository_path, path=path, comparison=comparison, expected_revision=expected_revision
            )
        )

    @server.post(
        "/api/threads/{thread_id}/host-git-captures",
        response_model=FileCapture,
        openapi_extra=_body(GitCaptureRequest),
    )
    async def capture_host_git_diff(thread_id: str, request: Request) -> FileCapture:
        app().require_host_git()
        return await app().capture_host_git_diff(
            thread_id=thread_id, request=await _document(request, GitCaptureRequest)
        )

    @server.get("/api/setup", response_model=SetupStatus)
    async def setup(rediscover: bool = False) -> SetupStatus:
        return await app().setup_status(rediscover=rediscover)

    @server.get("/api/models/choices", response_model=ModelChoices)
    async def model_choices() -> ModelChoices:
        return await app().model_choices()

    @server.get("/api/models/catalog", response_model=ModelCatalogSnapshot)
    async def model_catalog() -> ModelCatalogSnapshot:
        return await app().model_catalog()

    @server.post("/api/models/options", response_model=ModelOptions, openapi_extra=_body(ModelOptionsRequest))
    async def model_options(request: Request) -> ModelOptions:
        return await app().model_options(await _document(request, ModelOptionsRequest))

    @server.post("/api/models/prepare", response_model=ModelRecipe, openapi_extra=_body(ModelRecipeRequest))
    async def prepare_model(request: Request) -> ModelRecipe:
        return await app().prepare_model(await _document(request, ModelRecipeRequest))

    @server.post("/api/setup/preview", response_model=SetupPreview, openapi_extra=_body(SetupSelection))
    async def preview(request: Request) -> SetupPreview:
        return await app().preview_setup(await _document(request, SetupSelection))

    @server.post("/api/setup/apply", response_model=SetupPublication, openapi_extra=_body(SetupApplyRequest))
    async def apply(request: Request) -> SetupPublication:
        document = await _document(request, SetupApplyRequest)
        return await app().apply_setup(document.selection)

    @server.post(
        "/api/environments/preflight", response_model=EnvironmentReadiness, openapi_extra=_body(PreflightRequest)
    )
    async def preflight(request: Request) -> EnvironmentReadiness:
        document = await _document(request, PreflightRequest)
        readiness: EnvironmentReadiness | None = None
        async with create_task_group() as tasks:

            async def disconnected() -> None:
                while True:
                    incoming = await request.receive()
                    if incoming["type"] == "http.disconnect":
                        tasks.cancel_scope.cancel()
                        return

            tasks.start_soon(disconnected)
            readiness = await app().preflight_environment(document.profile_id, project_path=document.project_path)
            tasks.cancel_scope.cancel()
        if readiness is None:
            raise HarnessUiError("Environment preflight was cancelled.", code="preflight_cancelled")
        return readiness

    @server.get("/api/auth/accounts/{provider}", response_model=AccountProjection)
    async def account(provider: Provider) -> AccountProjection:
        return await app().inspect_model_account(provider)

    @server.delete("/api/auth/accounts/{provider}", response_model=bool)
    async def logout_account(provider: Provider) -> bool:
        return await app().logout_model_account(provider)

    @server.get("/api/catalog", response_model=tuple[CatalogReference, ...])
    async def implementation_catalog() -> tuple[CatalogReference, ...]:
        return await app().list_catalog()

    @server.get("/api/agents/{agent_id}/tool-proxy", response_model=AgentToolProxyView)
    async def agent_tool_proxy(agent_id: str) -> AgentToolProxyView:
        return await app().inspect_agent_tool_proxy(agent_id)

    @server.get("/api/auth/keys", response_model=tuple[ApiKeyStatus, ...])
    async def api_keys() -> tuple[ApiKeyStatus, ...]:
        return await app().list_api_keys()

    @server.put("/api/auth/keys", response_model=ApiKeyStatus, openapi_extra=_body(ApiKeyInput))
    async def put_api_key(request: Request) -> ApiKeyStatus:
        return await app().put_api_key(await _document(request, ApiKeyInput))

    @server.delete("/api/auth/keys/{reference}")
    async def delete_api_key(reference: str) -> None:
        await app().delete_api_key(reference)

    @server.get("/api/auth/logins", response_model=LoginStatus | None)
    async def active_login() -> LoginStatus | None:
        return await app().active_login()

    @server.post("/api/auth/logins", response_model=LoginStatus, openapi_extra=_body(LoginRequest))
    async def start_login(request: Request) -> LoginStatus:
        return await app().start_login(await _document(request, LoginRequest))

    @server.get("/api/auth/logins/{session_id}", response_model=LoginStatus)
    async def login_status(session_id: str) -> LoginStatus:
        return await app().login_status(session_id)

    @server.delete("/api/auth/logins/{session_id}", response_model=LoginStatus)
    async def cancel_login(session_id: str) -> LoginStatus:
        return await app().cancel_login(session_id)

    @server.get("/api/configuration/sources", response_model=ConfigurationSourceCatalog)
    async def configuration_sources() -> ConfigurationSourceCatalog:
        return await app().configuration_sources()

    @server.get("/api/configuration/sources/{relative_path:path}", response_model=ConfigurationSourceView)
    async def configuration_source(relative_path: str) -> ConfigurationSourceView:
        return await app().configuration_source(relative_path=relative_path)

    @server.post(
        "/api/configuration/validate",
        response_model=ConfigurationValidation,
        openapi_extra=_body(ResourceMutationRequest),
    )
    async def validate_source(
        request: Request, path: Annotated[str, Query(min_length=1, max_length=4096)]
    ) -> ConfigurationValidation:
        return await app().validate_configuration(
            relative_path=path, request=await _document(request, ResourceMutationRequest)
        )

    @server.put(
        "/api/configuration/sources/{relative_path:path}",
        response_model=ConfigurationPublication,
        openapi_extra=_body(ResourceMutationRequest),
    )
    async def put_source(relative_path: str, request: Request) -> ConfigurationPublication:
        result = await app().mutate_configuration(
            relative_path=relative_path, request=await _document(request, ResourceMutationRequest)
        )
        return ConfigurationPublication.from_result(result)

    @server.delete("/api/configuration/sources/{relative_path:path}", response_model=ConfigurationPublication)
    async def delete_source(relative_path: str) -> ConfigurationPublication:
        return ConfigurationPublication.from_result(await app().delete_configuration(relative_path=relative_path))

    @server.post("/api/threads/preview", response_model=ThreadConfiguration, openapi_extra=_body(NewThreadDefaults))
    async def preview_thread(request: Request) -> ThreadConfiguration:
        return await app().preview_thread_configuration(defaults=await _document(request, NewThreadDefaults))

    @server.post(
        "/api/threads/configuration-preview",
        response_model=ThreadConfigurationResolution,
        openapi_extra=_body(NewThreadDefaults),
    )
    async def explain_creation(request: Request) -> ThreadConfigurationResolution:
        return await app().explain_thread_configuration(defaults=await _document(request, NewThreadDefaults))

    @server.post("/api/threads/skills-preview", response_model=SkillCatalogView, openapi_extra=_body(NewThreadDefaults))
    async def preview_skills(request: Request) -> SkillCatalogView:
        return await app().skill_catalog(defaults=await _document(request, NewThreadDefaults))

    @server.get("/api/threads/{thread_id}/skills", response_model=SkillCatalogView)
    async def thread_skills(thread_id: str) -> SkillCatalogView:
        return await app().skill_catalog(thread_id=thread_id)

    @server.get("/api/threads/{thread_id}/configuration", response_model=ThreadConfigurationInspection)
    async def inspect_configuration(thread_id: str) -> ThreadConfigurationInspection:
        return await app().inspect_thread_configuration(thread_id)

    @server.get("/api/operations/{receipt_id}/configuration", response_model=CapturedConfiguration | None)
    async def operation_configuration(receipt_id: str) -> CapturedConfiguration | None:
        return await app().inspect_operation_configuration(receipt_id)

    @server.post(
        "/api/threads/{thread_id}/comments", response_model=OutputComment, openapi_extra=_body(CommentPublication)
    )
    async def publish_comment(thread_id: str, request: Request) -> OutputComment:
        return await app().publish_output_comment(thread_id, await _document(request, CommentPublication))

    @server.get("/api/threads/{thread_id}/comments", response_model=CommentPage)
    async def comments(
        thread_id: str,
        cursor: Annotated[str | None, Query(max_length=4096)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        target: Annotated[str | None, Query(max_length=2048)] = None,
        newest_first: bool = False,
    ) -> CommentPage:
        try:
            selected = SavedOutputTarget.model_validate_json(target) if target is not None else None
        except ValidationError:
            raise HarnessUiError("Target query does not match the schema.", code="request_invalid") from None
        return await app().list_output_comments(
            thread_id, target=selected, cursor=cursor, limit=limit, newest_first=newest_first
        )

    @server.get("/api/threads/{thread_id}/comments/{comment_id}", response_model=OutputComment)
    async def comment(thread_id: str, comment_id: str) -> OutputComment:
        return await app().get_output_comment(thread_id, comment_id)

    @server.patch(
        "/api/threads/{thread_id}/comments/{comment_id}",
        response_model=OutputComment,
        openapi_extra=_body(CommentEdit),
    )
    async def edit_comment(thread_id: str, comment_id: str, request: Request) -> OutputComment:
        return await app().edit_output_comment(thread_id, comment_id, await _document(request, CommentEdit))

    @server.delete("/api/threads/{thread_id}/comments/{comment_id}", status_code=204)
    async def delete_comment(
        thread_id: str, comment_id: str, expected_version: Annotated[int, Query(ge=1)]
    ) -> Response:
        await app().delete_output_comment(thread_id, comment_id, expected_version=expected_version)
        return Response(status_code=204)

    @server.post("/api/threads/{thread_id}/comments/{comment_id}/capture", response_model=ThreadAttachment)
    async def capture_comment(
        thread_id: str, comment_id: str, expected_version: Annotated[int | None, Query(ge=1)] = None
    ) -> ThreadAttachment:
        return await app().capture_output_comment(thread_id, comment_id, expected_version=expected_version)

    @server.post(
        "/api/threads/{thread_id}/saved-output", response_model=SavedOutputView, openapi_extra=_body(SavedOutputTarget)
    )
    async def saved_output(
        thread_id: str,
        request: Request,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=65536)] = 65536,
    ) -> SavedOutputView:
        return await app().read_commented_output(
            thread_id, await _document(request, SavedOutputTarget), offset=offset, limit=limit
        )

    @server.get("/api/threads/{thread_id}/children/{execution_id}/saved-output", response_model=SavedChildOutputPage)
    async def saved_child_output(
        thread_id: str,
        execution_id: str,
        cursor: Annotated[str | None, Query(max_length=4096)] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
    ) -> SavedChildOutputPage:
        return await app().saved_child_outputs(thread_id, execution_id, cursor=cursor, limit=limit)

    @server.get("/api/threads/{thread_id}/context-usage", response_model=ContextUsageView)
    async def context_usage(thread_id: str) -> ContextUsageView:
        return await app().context_usage(thread_id)

    @server.get("/api/threads/{thread_id}/usage", response_model=ThreadUsageView)
    async def usage(thread_id: str) -> ThreadUsageView:
        return await app().thread_usage(thread_id=thread_id)

    @server.get("/api/threads/{thread_id}/notes", response_model=NotePage)
    async def notes(thread_id: str, expected_continuation_id: str | None = None) -> NotePage:
        return await app().thread_notes(thread_id=thread_id, expected_continuation_id=expected_continuation_id)

    @server.patch(
        "/api/threads/{thread_id}/configuration",
        response_model=ThreadSummary,
        openapi_extra=_body(ThreadConfigurationMutationInput),
    )
    async def patch_configuration(thread_id: str, request: Request) -> ThreadSummary:
        return await app().patch_thread_configuration(
            thread_id=thread_id, mutation=await _document(request, ThreadConfigurationMutationInput)
        )

    @server.get(
        "/api/threads/{thread_id}/project-defaults",
        response_model=ProjectDefaultsPreview,
        response_model_exclude_unset=True,
    )
    async def project_defaults(thread_id: str) -> ProjectDefaultsPreview:
        return await app().preview_project_defaults(thread_id=thread_id)

    @server.post(
        "/api/threads/{thread_id}/project-defaults",
        response_model=ThreadSummary,
        openapi_extra=_body(ProjectDefaultsApply),
    )
    async def apply_project_defaults(thread_id: str, request: Request) -> ThreadSummary:
        return await app().apply_project_defaults(
            thread_id=thread_id, request=await _document(request, ProjectDefaultsApply)
        )

    @server.get("/api/projects", response_model=tuple[ProjectSummary, ...])
    async def projects() -> tuple[ProjectSummary, ...]:
        return await app().projects()

    @server.get("/api/threads/{thread_id}/decisions", response_model=DecisionBatchView | None)
    async def decision_batch(
        thread_id: str, expected_continuation_id: Annotated[str | None, Query(max_length=80)] = None
    ) -> DecisionBatchView | None:
        return await app().thread_decisions(thread_id=thread_id, expected_continuation_id=expected_continuation_id)

    @server.get("/api/selectors", response_model=ThreadSelectorCatalog)
    async def selectors() -> ThreadSelectorCatalog:
        return await app().thread_selectors()

    @server.post("/api/threads/lookup", response_model=ThreadPage, openapi_extra=_body(ThreadLookup))
    async def lookup_threads(request: Request) -> ThreadPage:
        query = await _document(request, ThreadLookup)
        return await app().lookup_threads(thread_ids=query.thread_ids)

    @server.post(
        "/api/threads/activity/lookup", response_model=tuple[ThreadActivityView, ...], openapi_extra=_body(ThreadLookup)
    )
    async def lookup_thread_activity(request: Request) -> tuple[ThreadActivityView, ...]:
        query = await _document(request, ThreadLookup)
        return await app().lookup_thread_activity(thread_ids=query.thread_ids)

    @server.get("/api/threads/activity", response_model=ThreadActivityPage)
    async def thread_activity(
        project_id: Annotated[str | None, Query(max_length=128)] = None,
        project_scope: Literal["all", "projectless", "unavailable"] = "all",
        query: Annotated[str | None, Query(max_length=512)] = None,
        include_archived: bool = False,
        archived_only: bool = False,
        include_active: bool = False,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> ThreadActivityPage:
        return await app().thread_activity(
            project_id=project_id,
            project_scope=project_scope,
            query=query,
            include_archived=include_archived,
            archived_only=archived_only,
            include_active=include_active,
            cursor=cursor,
            limit=limit,
        )

    @server.get("/api/threads/{thread_id}/tasks", response_model=TaskPage)
    async def tasks(
        thread_id: str,
        expected_continuation_id: Annotated[str | None, Query(max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> TaskPage:
        return await app().thread_tasks(
            thread_id=thread_id, expected_continuation_id=expected_continuation_id, limit=limit
        )

    @server.get("/api/threads/{thread_id}/children", response_model=ChildExecutionPage)
    async def children(
        thread_id: str,
        execution_id: Annotated[str | None, Query(max_length=80)] = None,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> ChildExecutionPage:
        return await app().query_child_executions(
            parent_thread_id=thread_id, execution_id=execution_id, cursor=cursor, limit=limit
        )

    @server.get("/api/threads/{thread_id}/children/wait", response_model=ChildExecutionPage)
    async def wait_children(
        thread_id: str,
        execution_id: Annotated[str | None, Query(max_length=80)] = None,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        timeout_seconds: Annotated[float, Query(ge=0, le=60)] = 10,
    ) -> ChildExecutionPage:
        return await app().wait_child_executions(
            parent_thread_id=thread_id,
            execution_id=execution_id,
            cursor=cursor,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    @server.get("/api/threads/{thread_id}/children/{execution_id}/review", response_model=ReviewView)
    async def child_review(thread_id: str, execution_id: str) -> ReviewView:
        return await app().child_review(parent_thread_id=thread_id, execution_id=execution_id)

    @server.post(
        "/api/threads/{thread_id}/children/{execution_id}/steer",
        response_model=ChildControlResult,
        openapi_extra=_body(SteerRequest),
    )
    async def steer_child(thread_id: str, execution_id: str, request: Request) -> ChildControlResult:
        body = await _document(request, SteerRequest)
        return await app().steer_child_execution(
            parent_thread_id=thread_id, execution_id=execution_id, message=body.prompt
        )

    @server.post("/api/threads/{thread_id}/children/{execution_id}/cancel", response_model=ChildControlResult)
    async def cancel_child(thread_id: str, execution_id: str) -> ChildControlResult:
        return await app().cancel_child_execution(parent_thread_id=thread_id, execution_id=execution_id)

    @server.get("/api/threads", response_model=ThreadPage)
    async def threads(
        query: Annotated[str | None, Query(max_length=500)] = None,
        project_id: str | None = None,
        include_archived: bool = False,
        sort: Literal["updated", "activity", "touched"] = "updated",
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> ThreadPage:
        return await app().list_threads(
            query=query, project_id=project_id, include_archived=include_archived, sort=sort, cursor=cursor, limit=limit
        )

    @server.post("/api/threads", response_model=ThreadSummary, openapi_extra=_body(CreateThreadRequest))
    async def create(request: Request) -> ThreadSummary:
        document = await _document(request, CreateThreadRequest)
        return await app().create_thread(defaults=document.defaults, title=document.title, thread_id=document.thread_id)

    @server.get("/api/threads/{thread_id}", response_model=ThreadDetail)
    async def thread(thread_id: str) -> ThreadDetail:
        return await app().get_thread(thread_id)

    @server.get("/api/threads/{thread_id}/transcript", response_model=TranscriptPage)
    async def transcript(
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        turn_id: str | None = None,
    ) -> TranscriptPage:
        return await app().get_thread_transcript(
            thread_id=thread_id,
            expected_continuation_id=expected_continuation_id,
            cursor=cursor,
            limit=limit,
            turn_id=turn_id,
        )

    @server.get("/api/threads/{thread_id}/inputs", response_model=TranscriptInputPage)
    async def transcript_inputs(
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> TranscriptInputPage:
        return await app().get_thread_inputs(
            thread_id=thread_id,
            expected_continuation_id=expected_continuation_id,
            cursor=cursor,
            limit=limit,
        )

    @server.post("/api/threads/{thread_id}/touch", response_model=ThreadSummary)
    async def touch_thread(thread_id: str) -> ThreadSummary:
        return await app().touch_thread(thread_id)

    @server.patch(
        "/api/threads/{thread_id}/metadata", response_model=ThreadSummary, openapi_extra=_body(ThreadMetadataMutation)
    )
    async def metadata(thread_id: str, request: Request) -> ThreadSummary:
        return await app().update_thread_metadata(
            thread_id=thread_id, mutation=await _document(request, ThreadMetadataMutation)
        )

    @server.post(
        "/api/threads/{thread_id}/attachments",
        response_model=ThreadAttachment,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def upload_attachment(
        thread_id: str, request: Request, name: Annotated[str, Query(min_length=1, max_length=255)]
    ) -> ThreadAttachment:
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_ATTACHMENT_BYTES:
                raise HarnessUiError("Attachment exceeds 10 MiB.", code="request_too_large")
            data.extend(chunk)
        media_type = request.headers.get("content-type", "").split(";", 1)[0]
        try:
            return await app().stage_thread_attachment(
                thread_id=thread_id,
                upload=AttachmentUpload(
                    name, bytes(data), media_type if media_type and media_type != "application/octet-stream" else None
                ),
            )
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="attachment_invalid") from exc

    @server.get("/api/threads/{thread_id}/attachments/{attachment_id}/metadata", response_model=ThreadAttachment)
    async def attachment_metadata(thread_id: str, attachment_id: str) -> ThreadAttachment:
        try:
            attachment, _ = await app().read_thread_attachment(thread_id=thread_id, attachment_id=attachment_id)
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="attachment_invalid") from exc
        return attachment

    @server.get("/api/threads/{thread_id}/attachments/{attachment_id}")
    async def download_attachment(thread_id: str, attachment_id: str) -> Response:
        try:
            attachment, data = await app().read_thread_attachment(thread_id=thread_id, attachment_id=attachment_id)
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="attachment_invalid") from exc
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.name, safe='')}",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @server.post("/api/threads/{thread_id}/submit", response_model=RootRunReceipt, openapi_extra=_body(SubmitRequest))
    async def submit(thread_id: str, request: Request) -> RootRunReceipt:
        document = await _document(request, SubmitRequest)
        try:
            return await app().submit_thread(
                thread_id=thread_id,
                prompt=document.input(),
                attachment_ids=document.attachment_ids,
                environment_profile_id=document.environment_profile_id,
                model_overrides=RunModelOverrides(
                    model_id=document.model_id, thinking=document.thinking, fast=document.fast
                ),
                skill_references=document.skill_references,
                input_surface="webui",
            )
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="input_invalid") from exc

    @server.post(
        "/api/threads/{thread_id}/decisions", response_model=RootRunReceipt, openapi_extra=_body(DecisionResponseBatch)
    )
    async def decisions(thread_id: str, request: Request) -> RootRunReceipt:
        return await app().respond_decisions(
            thread_id=thread_id, response=await _document(request, DecisionResponseBatch)
        )

    @server.get("/api/operations/{receipt_id}", response_model=RootOperationView)
    async def operation(receipt_id: str) -> RootOperationView:
        return await app().get_root_operation(receipt_id)

    @server.post(
        "/api/operations/{receipt_id}/steer", response_model=RootControlResult, openapi_extra=_body(RootSteerRequest)
    )
    async def steer(receipt_id: str, request: Request) -> RootControlResult:
        document = await _document(request, RootSteerRequest)
        try:
            return await app().steer_root_operation(
                receipt_id=receipt_id,
                message=document.input(),
                attachment_ids=document.attachment_ids,
                skill_references=document.skill_references,
            )
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="input_invalid") from exc

    @server.post("/api/operations/{receipt_id}/cancel", response_model=RootControlResult)
    async def cancel(receipt_id: str) -> RootControlResult:
        return await app().cancel_root_operation(receipt_id)

    async def focus_frames(
        thread_id: str, after: str | None
    ) -> AsyncGenerator[FocusSnapshotFrame | FocusReplayFrame | FocusReadyFrame | FocusEventFrame | ResetFrame]:
        try:
            if after is not None:
                parsed = _parse_cursor(after, "focus", thread_id)
                async with app().live_events(
                    root_thread_id=thread_id, after=LiveCursor(epoch=parsed.epoch, sequence=parsed.sequence)
                ) as subscription:
                    async for event in subscription:
                        yield FocusEventFrame(
                            event=event, resume_cursor=_cursor("focus", thread_id, event.epoch, event.sequence)
                        )
            else:
                async with app().watch_thread(root_thread_id=thread_id) as watch:
                    yield FocusSnapshotFrame(
                        snapshot=watch.snapshot,
                        resume_cursor=(
                            _cursor("focus", thread_id, watch.snapshot.epoch, watch.snapshot.cutover_sequence)
                            if watch.root_stream is None
                            else None
                        ),
                    )
                    if watch.root_stream is not None:
                        for batch in watch.root_stream.batches():
                            yield FocusReplayFrame(run_id=watch.root_stream.summary.run_id, events=batch)
                        yield FocusReadyFrame(
                            resume_cursor=_cursor(
                                "focus", thread_id, watch.snapshot.epoch, watch.snapshot.cutover_sequence
                            )
                        )
                    async for event in watch.events:
                        yield FocusEventFrame(
                            event=event, resume_cursor=_cursor("focus", thread_id, event.epoch, event.sequence)
                        )
        except HarnessUiError as exc:
            yield ResetFrame(reason=exc.code)

    async def summary_frames(after: str | None) -> AsyncGenerator[SummaryOpenFrame | SummaryEventFrame | ResetFrame]:
        try:
            parsed = None if after is None else _parse_cursor(after, "summary", None)
            cursor = None if parsed is None else SummaryCursor(epoch=parsed.epoch, sequence=parsed.sequence)
            async with app().summary_events(after=cursor) as subscription:
                cutover = subscription.cursor
                yield SummaryOpenFrame(
                    cursor=cutover,
                    resume_cursor=_cursor("summary", None, cutover.epoch, cutover.sequence),
                    resumed=after is not None,
                )
                async for event in subscription:
                    yield SummaryEventFrame(
                        event=event, resume_cursor=_cursor("summary", None, event.epoch, event.sequence)
                    )
        except HarnessUiError as exc:
            yield ResetFrame(reason=exc.code)

    @server.websocket("/api/realtime/connect")
    async def realtime(socket: WebSocket) -> None:
        if not await authenticate_interactive(socket, api_key):
            return
        channels: dict[str, tuple[CancelScope, str | None]] = {}
        sending = Lock()
        closing = False

        async def send(frame: RealtimeFrame | RealtimePing) -> None:
            # A stalled connection cannot retain channel tasks indefinitely.
            with fail_after(10):
                async with sending:
                    if closing:
                        raise WebSocketDisconnect(code=1001)
                    await socket.send_text(frame.model_dump_json())

        async def close(*, code: int, reason: str = "") -> None:
            nonlocal closing
            with fail_after(10):
                async with sending:
                    if closing:
                        return
                    # The transport marks itself disconnected before its close
                    # send completes; observers must share this lock and state.
                    closing = True
                    await socket.close(code=code, reason=reason)

        try:
            async with create_task_group() as group:

                async def observe(command: RealtimeCommand, *, task_status: TaskStatus[None]) -> None:
                    with CancelScope() as scope:
                        channels[command.channel] = (scope, command.root_thread_id)
                        task_status.started()
                        frames = (
                            focus_frames(command.root_thread_id, command.after)
                            if command.root_thread_id is not None
                            else summary_frames(command.after)
                        )
                        try:
                            async for frame in frames:
                                await send(RealtimeFrame(channel=command.channel, frame=frame))
                        finally:
                            await frames.aclose()
                            current = channels.get(command.channel)
                            if current is not None and current[0] is scope:
                                channels.pop(command.channel, None)

                async def trim_idle_channels() -> set[str]:
                    active = set(await app().active_root_thread_ids())
                    idle = [channel for channel, (_, root) in channels.items() if root not in active]
                    # Active capacity follows admitted work, not an arbitrary tab
                    # limit. Completed roots return to the finite idle allowance.
                    expired = [(channel, channels.pop(channel)[0]) for channel in idle[:-12]]
                    for _, scope in expired:
                        scope.cancel()
                    # Detach the entire batch before yielding: heartbeat and
                    # command handling can trim or unsubscribe concurrently.
                    for channel, _ in expired:
                        await send(RealtimeFrame(channel=channel, frame=ResetFrame(reason="channel_limit")))
                    return active

                async def heartbeat() -> None:
                    while not stopping.is_set():
                        await trim_idle_channels()
                        await send(RealtimePing())
                        with move_on_after(20):
                            await stopping.wait()
                    await close(code=1001)
                    group.cancel_scope.cancel()

                group.start_soon(heartbeat)
                try:
                    while True:
                        with fail_after(60):
                            raw = await receive_text(socket, limit=4096)
                        command = RealtimeCommand.model_validate_json(raw)
                        if command.kind == "pong":
                            continue
                        previous = channels.pop(command.channel, None)
                        if previous is not None:
                            previous[0].cancel()
                        if command.kind == "subscribe":
                            active = await trim_idle_channels()
                            duplicate = any(root == command.root_thread_id for _, root in channels.values())
                            idle_count = sum(root not in active for _, root in channels.values())
                            if duplicate or (command.root_thread_id not in active and idle_count >= 12):
                                await send(
                                    RealtimeFrame(channel=command.channel, frame=ResetFrame(reason="channel_limit"))
                                )
                                continue
                            await group.start(observe, command)
                except (ValidationError, ValueError):
                    await close(code=4400, reason="Invalid realtime command")
                except TimeoutError:
                    await close(code=4408, reason="Realtime heartbeat timed out")
                except WebSocketDisconnect:
                    pass
                finally:
                    group.cancel_scope.cancel()
        except* (WebSocketDisconnect, TimeoutError):
            pass

    @server.get("/api/openapi.json", include_in_schema=False)
    async def schema() -> JSONResponse:
        return JSONResponse(openapi_document(server))

    @server.get("/{path:path}", include_in_schema=False, response_model=None)
    async def static(path: str) -> FileResponse | JSONResponse:
        install_assets = {
            "manifest.webmanifest": "application/manifest+json",
            "sw.js": "text/javascript",
            "icons/icon-192.png": "image/png",
            "icons/icon-512.png": "image/png",
            "icons/icon-maskable-512.png": "image/png",
            "icons/apple-touch-icon.png": "image/png",
        }
        if path in install_assets:
            destination = static_root / path
            if not destination.is_file():
                return _error("not_found", "Asset not found.", 404)
            return FileResponse(destination, media_type=install_assets[path], headers={"Cache-Control": "no-cache"})
        if path.startswith("assets/"):
            destination = (static_root / path).resolve()
            if destination.is_relative_to(static_root.resolve()) and destination.is_file():
                return FileResponse(destination, headers={"Cache-Control": "public, max-age=31536000, immutable"})
            return _error("not_found", "Asset not found.", 404)
        segments = path.split("/")
        recognized = path in {
            "",
            "new",
            "setup",
            "settings",
            "projects",
            "settings/resources",
            "settings/source",
            "settings/accounts",
            "settings/catalog",
            "settings/models",
            "settings/notifications",
        } or (len(segments) == 2 and segments[0] in {"threads", "projects", "new"} and bool(segments[1]))
        if not recognized:
            return _error("not_found", "Route not found.", 404)
        index = static_root / "index.html"
        if not index.is_file():
            return _error(
                "assets_unavailable",
                "Bundled browser assets are unavailable. Build frontend/apps/a13n-harness-ui before source development.",
                503,
            )
        return FileResponse(
            index,
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            },
        )

    return server


def openapi_document(server: FastAPI) -> dict[str, Any]:
    """Lift strict JSON request definitions into one generated schema authority."""
    document = server.openapi()
    components = document.setdefault("components", {}).setdefault("schemas", {})
    for model in (
        InteractiveAuthentication,
        RealtimeCommand,
        RealtimeFrame,
        RealtimePing,
        TerminalCommand,
        TerminalFrame,
        DraftCommand,
        DraftFrame,
        PresenceReport,
        PresenceFrame,
        PointerReport,
        PointerFrame,
        ErrorEnvelope,
    ):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(schema.pop("$defs", {}))
        components[model.__name__] = schema
    document["x-interactive"] = {
        "authentication": {"$ref": "#/components/schemas/InteractiveAuthentication"},
        "realtime": {
            "path": "/api/realtime/connect",
            "input": {"$ref": "#/components/schemas/RealtimeCommand"},
            "output": {"$ref": "#/components/schemas/RealtimeFrame"},
            "heartbeat": {"$ref": "#/components/schemas/RealtimePing"},
        },
        "presence": {
            "path": "/api/presence/connect",
            "input": {"$ref": "#/components/schemas/PresenceReport"},
            "output": {"$ref": "#/components/schemas/PresenceFrame"},
            "pointer_input": {"$ref": "#/components/schemas/PointerReport"},
            "pointer_output": {"$ref": "#/components/schemas/PointerFrame"},
            "error": {"$ref": "#/components/schemas/ErrorEnvelope"},
            "report_timeout_seconds": PRESENCE_TIMEOUT_SECONDS,
        },
        "draft": {
            "path": "/api/threads/{thread_id}/draft/connect",
            "input": {"$ref": "#/components/schemas/DraftCommand"},
            "output": {"$ref": "#/components/schemas/DraftFrame"},
            "error": {"$ref": "#/components/schemas/ErrorEnvelope"},
        },
        "terminal": {
            "path": "/api/host/terminals/{terminal_id}/connect",
            "input": {"$ref": "#/components/schemas/TerminalCommand"},
            "output": {"$ref": "#/components/schemas/TerminalFrame"},
            "error": {"$ref": "#/components/schemas/ErrorEnvelope"},
        },
    }
    for path in document["paths"].values():
        for operation in path.values():
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body is not None:
                components.update(body.pop("$defs", {}))
                if "title" in body:
                    components[body["title"]] = body
                    operation["requestBody"]["content"]["application/json"]["schema"] = {
                        "$ref": f"#/components/schemas/{body['title']}"
                    }
    return document


async def run(
    app_factory: AppFactory,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    api_key: str | None = None,
    dangerously_bypass_permission: bool = False,
) -> None:
    """Serve in the foreground; disconnecting browsers never ends this owner."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise HarnessUiError("--host requires an IPv4 or IPv6 address.", code="webui_host_invalid") from None
    if api_key is None:
        api_key = os.environ.get("A13N_HARNESS_UI_API_KEY")
    if dangerously_bypass_permission and api_key is not None:
        raise HarnessUiError(
            "--dangerous-skip-permissions cannot be combined with a CLI or environment API key.",
            code="webui_access_conflict",
        )
    if api_key == "":
        raise HarnessUiError("--apikey or A13N_HARNESS_UI_API_KEY cannot be empty.", code="webui_key_invalid")
    generated = api_key is None and not dangerously_bypass_permission
    selected_key = None if dangerously_bypass_permission else (api_key or secrets.token_urlsafe(32))
    browser_ip = ("::1" if address.version == 6 else "127.0.0.1") if address.is_unspecified else host
    browser_host = f"[{browser_ip}]" if address.version == 6 else browser_ip
    url = f"http://{browser_host}:{port}/"
    click.echo(f"WebUI {__version__}: {url}")
    if dangerously_bypass_permission:
        click.echo("WARNING: API authentication is disabled. Every reachable client has full App access.", err=True)
    elif generated:
        assert selected_key is not None
        click.echo(f"API key: {selected_key}\nOpen: {url}#api_key={quote(selected_key, safe='')}")
    else:
        click.echo("Using supplied API key (not echoed). Shell history and process arguments may expose it.", err=True)
    if not address.is_loopback:
        click.echo(
            "WARNING: non-loopback plain HTTP grants shared instance access on a trusted network, not tenant isolation. Use external TLS when needed.",
            err=True,
        )
    stopping = Event()
    server = create_webui(app_factory, api_key=selected_key, host=host, stopping=stopping)
    await WebUIServer(
        uvicorn.Config(
            server,
            host=host,
            port=port,
            access_log=False,
            log_config=None,
            log_level="warning",
            timeout_graceful_shutdown=3,
            ws="websockets-sansio",
            ws_max_size=1024 * 1024,
        ),
        stopping=stopping,
    ).serve()
