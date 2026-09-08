"""Same-origin HTTP delivery over one in-memory WebUI App lifetime."""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit

import click
import uvicorn
from anyio import create_task_group
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.types import ASGIApp, Receive, Scope, Send

from a13n_harness_ui.app import AppStatus, HarnessUiApp
from a13n_harness_ui.configuration.setup import SetupPreview, SetupPublication, SetupSelection
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.live import LiveCursor, LiveEvent, SummaryCursor, SummaryInvalidation
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStatus
from a13n_harness_ui.model_accounts.login import LoginRequest, LoginStatus
from a13n_harness_ui.setup import EnvironmentReadiness, SetupStatus
from a13n_harness_ui.surfaces import (
    DecisionBatchView,
    DecisionResponseBatch,
    NewThreadDefaults,
    ProjectSummary,
    RootControlResult,
    RootOperationView,
    RootRunReceipt,
    SurfaceModel,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadMetadataMutation,
    ThreadPage,
    ThreadSelectorCatalog,
    ThreadSummary,
    TranscriptPage,
)
from a13n_harness_ui.thread_files import MAX_ATTACHMENT_BYTES, AttachmentUpload, ThreadAttachment

API_VERSION = "1"
_MAX_BODY = 1024 * 1024
_STATIC = Path(__file__).parent / "static"
AppFactory = Callable[[], AbstractAsyncContextManager[HarnessUiApp]]


class ListenerStatus(SurfaceModel):
    api_version: Literal["1"] = "1"
    app: AppStatus
    host: str
    access: Literal["api_key", "dangerous_bypass"]


class CreateThreadRequest(SurfaceModel):
    defaults: NewThreadDefaults | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)


class PromptRequest(SurfaceModel):
    prompt: str = Field(default="", max_length=256 * 1024)
    attachment_ids: tuple[str, ...] = Field(default=(), max_length=8)


class SetupApplyRequest(SurfaceModel):
    selection: SetupSelection


class PreflightRequest(SurfaceModel):
    profile_id: Literal["environment-native", "environment-sandbox"]
    project_path: str = Field(min_length=1, max_length=4096)


class FocusSnapshotFrame(SurfaceModel):
    kind: Literal["snapshot"] = "snapshot"
    snapshot: ThreadFocusSnapshot
    resume_cursor: str


class FocusEventFrame(SurfaceModel):
    kind: Literal["event"] = "event"
    event: LiveEvent
    resume_cursor: str


class ResetFrame(SurfaceModel):
    kind: Literal["reset"] = "reset"
    reason: str


class SummaryOpenFrame(SurfaceModel):
    kind: Literal["open"] = "open"
    cursor: SummaryCursor
    resume_cursor: str


class SummaryEventFrame(SurfaceModel):
    kind: Literal["invalidation"] = "invalidation"
    event: SummaryInvalidation
    resume_cursor: str


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
    return JSONResponse(
        ErrorEnvelope(error=ErrorBody(code=code, message=message)).model_dump(),
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


class AccessBoundary:
    """Authenticate before body parsing or App access, without logging secrets."""

    def __init__(self, app: ASGIApp, *, api_key: str | None, allowed_hosts: frozenset[str]) -> None:
        self.app = app
        self.api_key = api_key
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        host = request.url.hostname
        wildcard_ip = False
        if {"0.0.0.0", "::"} & self.allowed_hosts and host is not None:
            try:
                ipaddress.ip_address(host)
                wildcard_ip = True
            except ValueError:
                pass
        if host not in self.allowed_hosts and not wildcard_ip:
            await _error("host_rejected", "Use the listener's explicit browser address.", 400)(scope, receive, send)
            return
        if scope["path"] == "/api" or scope["path"].startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme != request.url.scheme
                    or parsed.netloc != request.headers.get("host")
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                ):
                    await _error("origin_rejected", "Cross-origin API access is not enabled.", 403)(
                        scope, receive, send
                    )
                    return
            authorization = request.headers.get("authorization", "")
            if self.api_key is not None and not hmac.compare_digest(
                authorization.encode(), f"Bearer {self.api_key}".encode()
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


def _frame(model: BaseModel) -> str:
    return f"data: {model.model_dump_json()}\n\n"


def create_webui(
    app_factory: AppFactory, *, api_key: str | None, host: str = "127.0.0.1", static_root: Path = _STATIC
) -> FastAPI:
    """Build the adapter; only its ASGI lifespan opens and owns the App."""
    if api_key == "":
        raise ValueError("API key cannot be empty")
    owner: HarnessUiApp | None = None

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
        if code == "request_too_large":
            status = 413
        elif code in {"app_not_ready", "app_stopping"}:
            status = 503
        elif code.endswith("not_found"):
            status = 404
        return _error(code, str(exc), status)

    @server.exception_handler(RequestValidationError)
    async def query_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error("request_invalid", "Query does not match the API schema.", 422)

    @server.get("/api/status", response_model=ListenerStatus)
    async def status() -> ListenerStatus:
        return ListenerStatus(
            app=await app().status(), host=host, access="api_key" if api_key is not None else "dangerous_bypass"
        )

    @server.get("/api/setup", response_model=SetupStatus)
    async def setup(rediscover: bool = False) -> SetupStatus:
        return await app().setup_status(rediscover=rediscover)

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

    @server.get("/api/auth/keys", response_model=tuple[ApiKeyStatus, ...])
    async def api_keys() -> tuple[ApiKeyStatus, ...]:
        return await app().list_api_keys()

    @server.put("/api/auth/keys", response_model=ApiKeyStatus, openapi_extra=_body(ApiKeyInput))
    async def put_api_key(request: Request) -> ApiKeyStatus:
        return await app().put_api_key(await _document(request, ApiKeyInput))

    @server.delete("/api/auth/keys/{reference}")
    async def delete_api_key(reference: str) -> None:
        await app().delete_api_key(reference)

    @server.post("/api/auth/logins", response_model=LoginStatus, openapi_extra=_body(LoginRequest))
    async def start_login(request: Request) -> LoginStatus:
        return await app().start_login(await _document(request, LoginRequest))

    @server.get("/api/auth/logins/{session_id}", response_model=LoginStatus)
    async def login_status(session_id: str) -> LoginStatus:
        return await app().login_status(session_id)

    @server.delete("/api/auth/logins/{session_id}", response_model=LoginStatus)
    async def cancel_login(session_id: str) -> LoginStatus:
        return await app().cancel_login(session_id)

    @server.get("/api/projects", response_model=tuple[ProjectSummary, ...])
    async def projects() -> tuple[ProjectSummary, ...]:
        return await app().projects()

    @server.get("/api/threads/{thread_id}/decisions", response_model=DecisionBatchView | None)
    async def decision_batch(thread_id: str) -> DecisionBatchView | None:
        return await app().thread_decisions(thread_id=thread_id)

    @server.get("/api/selectors", response_model=ThreadSelectorCatalog)
    async def selectors() -> ThreadSelectorCatalog:
        return await app().thread_selectors()

    @server.get("/api/threads", response_model=ThreadPage)
    async def threads(
        query: Annotated[str | None, Query(max_length=500)] = None,
        project_id: str | None = None,
        include_archived: bool = False,
        sort: Literal["updated", "activity"] = "updated",
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> ThreadPage:
        return await app().list_threads(
            query=query, project_id=project_id, include_archived=include_archived, sort=sort, cursor=cursor, limit=limit
        )

    @server.post("/api/threads", response_model=ThreadSummary, openapi_extra=_body(CreateThreadRequest))
    async def create(request: Request) -> ThreadSummary:
        document = await _document(request, CreateThreadRequest)
        return await app().create_thread(defaults=document.defaults, title=document.title)

    @server.get("/api/threads/{thread_id}", response_model=ThreadDetail)
    async def thread(thread_id: str) -> ThreadDetail:
        return await app().get_thread(thread_id)

    @server.get("/api/threads/{thread_id}/transcript", response_model=TranscriptPage)
    async def transcript(
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> TranscriptPage:
        return await app().get_thread_transcript(
            thread_id=thread_id, expected_continuation_id=expected_continuation_id, cursor=cursor, limit=limit
        )

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

    @server.post("/api/threads/{thread_id}/submit", response_model=RootRunReceipt, openapi_extra=_body(PromptRequest))
    async def submit(thread_id: str, request: Request) -> RootRunReceipt:
        document = await _document(request, PromptRequest)
        try:
            return await app().submit_thread(
                thread_id=thread_id, prompt=document.prompt, attachment_ids=document.attachment_ids
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
        "/api/operations/{receipt_id}/steer", response_model=RootControlResult, openapi_extra=_body(PromptRequest)
    )
    async def steer(receipt_id: str, request: Request) -> RootControlResult:
        document = await _document(request, PromptRequest)
        return await app().steer_root_operation(receipt_id=receipt_id, message=document.prompt)

    @server.post("/api/operations/{receipt_id}/cancel", response_model=RootControlResult)
    async def cancel(receipt_id: str) -> RootControlResult:
        return await app().cancel_root_operation(receipt_id)

    @server.get("/api/threads/{thread_id}/events", response_model=FocusSnapshotFrame | FocusEventFrame | ResetFrame)
    async def focused(thread_id: str, after: Annotated[str | None, Query(max_length=1024)] = None) -> StreamingResponse:
        # Validate before response headers; the watch remains owned by the
        # generator task so cancellation closes delivery, never the root Run.
        await app().get_thread(thread_id)

        async def events() -> AsyncIterator[str]:
            try:
                if after is not None:
                    parsed = _parse_cursor(after, "focus", thread_id)
                    async with app().live_events(
                        root_thread_id=thread_id, after=LiveCursor(epoch=parsed.epoch, sequence=parsed.sequence)
                    ) as subscription:
                        async for event in subscription:
                            yield _frame(
                                FocusEventFrame(
                                    event=event, resume_cursor=_cursor("focus", thread_id, event.epoch, event.sequence)
                                )
                            )
                else:
                    async with app().watch_thread(root_thread_id=thread_id) as watch:
                        yield _frame(
                            FocusSnapshotFrame(
                                snapshot=watch.snapshot,
                                resume_cursor=_cursor(
                                    "focus", thread_id, watch.snapshot.epoch, watch.snapshot.cutover_sequence
                                ),
                            )
                        )
                        async for event in watch.events:
                            yield _frame(
                                FocusEventFrame(
                                    event=event, resume_cursor=_cursor("focus", thread_id, event.epoch, event.sequence)
                                )
                            )
            except HarnessUiError as exc:
                yield _frame(ResetFrame(reason=exc.code))

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )

    @server.get("/api/events", response_model=SummaryOpenFrame | SummaryEventFrame | ResetFrame)
    async def summary(after: Annotated[str | None, Query(max_length=1024)] = None) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            try:
                parsed = None if after is None else _parse_cursor(after, "summary", None)
                cursor = None if parsed is None else SummaryCursor(epoch=parsed.epoch, sequence=parsed.sequence)
                async with app().summary_events(after=cursor) as subscription:
                    cutover = subscription.cursor
                    yield _frame(
                        SummaryOpenFrame(
                            cursor=cutover, resume_cursor=_cursor("summary", None, cutover.epoch, cutover.sequence)
                        )
                    )
                    async for event in subscription:
                        yield _frame(
                            SummaryEventFrame(
                                event=event, resume_cursor=_cursor("summary", None, event.epoch, event.sequence)
                            )
                        )
            except HarnessUiError as exc:
                yield _frame(ResetFrame(reason=exc.code))

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )

    @server.get("/api/openapi.json", include_in_schema=False)
    async def schema() -> JSONResponse:
        return JSONResponse(openapi_document(server))

    @server.get("/{path:path}", include_in_schema=False, response_model=None)
    async def static(path: str) -> FileResponse | JSONResponse:
        if path.startswith("assets/"):
            destination = (static_root / path).resolve()
            if destination.is_relative_to(static_root.resolve()) and destination.is_file():
                return FileResponse(destination, headers={"Cache-Control": "public, max-age=31536000, immutable"})
            return _error("not_found", "Asset not found.", 404)
        segments = path.split("/")
        recognized = path in {"", "setup", "settings"} or (
            len(segments) == 2 and segments[0] == "threads" and bool(segments[1])
        )
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
                "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            },
        )

    return server


def openapi_document(server: FastAPI) -> dict[str, Any]:
    """Lift strict JSON request definitions into one generated schema authority."""
    document = server.openapi()
    components = document.setdefault("components", {}).setdefault("schemas", {})
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
    if dangerously_bypass_permission and api_key is not None:
        raise HarnessUiError(
            "--api-key and --dangerously-bypass-permission cannot be combined.", code="webui_access_conflict"
        )
    if api_key == "":
        raise HarnessUiError("--api-key cannot be empty.", code="webui_key_invalid")
    generated = api_key is None and not dangerously_bypass_permission
    selected_key = None if dangerously_bypass_permission else (api_key or secrets.token_urlsafe(32))
    browser_host = f"[{host}]" if address.version == 6 else host
    url = f"http://{browser_host}:{port}/"
    click.echo(f"WebUI: {url}", err=True)
    if dangerously_bypass_permission:
        click.echo("WARNING: API authentication is disabled. Every reachable client has full App access.", err=True)
    elif generated:
        assert selected_key is not None
        click.echo(f"API key: {selected_key}\nOpen: {url}#api_key={quote(selected_key, safe='')}", err=True)
    else:
        click.echo("Using supplied API key (not echoed). Shell history and process arguments may expose it.", err=True)
    if not address.is_loopback:
        click.echo(
            "WARNING: non-loopback plain HTTP is a single-user trusted-network listener, not a multi-user service. Use external TLS when needed.",
            err=True,
        )
    server = create_webui(app_factory, api_key=selected_key, host=host)
    await uvicorn.Server(
        uvicorn.Config(server, host=host, port=port, access_log=False, log_level="warning", timeout_graceful_shutdown=3)
    ).serve()
