"""Open and interact with saved Apps outside the worker and without a database session across I/O."""

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import anyio
from a13n_harness import AgentContext, RunConfiguration
from a13n_harness.providers.connector.bounds import DISCOVERY_MAX_TOOLS
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_logging import get_logger
from mcp.server.apps import APP_MIME_TYPE
from mcp.types import INVALID_PARAMS, BlobResourceContents, ReadResourceResult, TextResourceContents
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.mcp import MCPError, MCPToolset
from sqlalchemy import select

from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools import ToolDispatch
from a13n_service.providers.tools.mcp import call_app_tool, mcp_client
from a13n_service.providers.tools.mcp_apps import require_classic
from a13n_service.resources.connections.access import McpConnection, resolve_connection
from a13n_service.resources.connections.oauth import open_mcp_client
from a13n_service.resources.connections.service import check_caller_headers
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.runs import checkpoints, runs
from a13n_service.runs.admission import AppCallContext
from a13n_service.runs.contents import MAX_APP_BYTES, ContentObject, decode
from a13n_service.runs.mcp_apps.schemas import (
    AppBinding,
    AppOriginal,
    AppResource,
    AppToolCall,
    AppToolResult,
    OpenedApp,
    SavedApp,
)
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import RunOptions
from a13n_service.runs.threads import get_run
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

logger = get_logger(__name__)


async def _reference(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, app_id: str) -> ContentObject:
    ref = await runs.content_ref(runtime, actor, workspace_id, run_id, app_id, purpose="app")
    if ref.app is None or ref.truncated or ref.media_type != "application/json" or ref.size_bytes > MAX_APP_BYTES:
        raise not_found("mcp_app", app_id)
    return ref


async def _original(runtime: Runtime, ref: ContentObject) -> SavedApp:
    assert ref.app is not None
    value = decode(await checkpoints.load(runtime.objects, ref), ref)
    original = AppOriginal.model_validate(value)
    if original.reference != ref.app.reference:
        raise not_found("mcp_app", ref.id)
    return SavedApp(**original.model_dump(), **ref.app.model_dump(exclude={"reference"}))


def _check_binding(saved: AppBinding, run_id: str, app_id: str) -> None:
    if saved.run_id != run_id or saved.reference.app_id != app_id:
        raise not_found("mcp_app", app_id)
    if saved.selection.connection_id != saved.reference.connection_id or saved.reference.unavailable:
        raise not_found("mcp_app", app_id)


async def original(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, app_id: str) -> SavedApp:
    ref = await _reference(runtime, actor, workspace_id, run_id, app_id)
    saved = await _original(runtime, ref)
    _check_binding(saved, run_id, app_id)
    return saved


async def _binding(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, app_id: str) -> AppBinding:
    ref = await _reference(runtime, actor, workspace_id, run_id, app_id)
    saved = ref.app
    assert saved is not None
    _check_binding(saved, run_id, app_id)
    return saved


@dataclass(frozen=True)
class _Connection:
    connection: McpConnection
    headers: dict[str, str]
    policy: EndpointPolicy


async def _resolve(
    runtime: Runtime, actor: Principal, workspace_id: str, saved: AppBinding, *, tool_name: str | None = None
) -> _Connection:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        run = await get_run(session, scope.workspace_id, saved.run_id)
        connection = await resolve_connection(session, actor, scope, saved.reference.connection_id, verb="run")
        if not isinstance(connection, McpConnection) or connection.config.url != saved.connection_url:
            raise conflict("mcp_app", saved.reference.app_id, "connection_changed")
        if connection.status != "ready":
            raise conflict("connection", connection.id, connection.status)
        if tool_name is not None:
            selection = saved.selection
            if any(
                names is not None and tool_name not in names for names in (selection.tools, connection.config.tools)
            ):
                raise ServiceError("forbidden", "The App tool is outside the selected connection tools")
            mode = selection.permissions.get(tool_name, selection.permission)
            if mode not in {"allow", "inherit"}:
                raise ServiceError("forbidden", "The App tool requires a Run permission flow", {"permission": mode})
        headers = dict(run.mcp_headers.get(connection.id, {}))
        check_caller_headers(connection.id, connection.config, headers)
        configuration = RunOptions.model_validate(run.options).configuration or RunConfiguration()
    return _Connection(connection, headers, runtime.endpoint_policy.for_run(configuration))


@asynccontextmanager
async def _toolset(runtime: Runtime, resolved: _Connection) -> AsyncIterator[MCPToolset[AgentContext]]:
    settings = runtime.settings.providers
    with anyio.fail_after(settings.tool_call_seconds):
        async with open_mcp_client(
            resolved.connection, storage=runtime.storage, keys=runtime.keys, policy=resolved.policy, settings=settings
        ) as http:
            http.headers.update(resolved.headers)
            async with mcp_client(
                resolved.connection.config.url,
                resolved.connection.id,
                http,
                timeout=settings.tool_call_seconds,
                apps=True,
            ) as client:
                # One request owns this catalog snapshot and its client. No state is shared with the worker.
                async with MCPToolset[AgentContext](
                    client,
                    id=resolved.connection.id,
                    prefer_tasks=False,
                    cache_tools=True,
                    cache_resources=False,
                    cache_prompts=False,
                    include_instructions=False,
                    max_retries=0,
                    tool_error_behavior="failed",
                ) as toolset:
                    yield toolset


async def _resource(toolset: MCPToolset[AgentContext], uri: str) -> AppResource:
    result = ReadResourceResult.model_validate(await toolset.read_resource_for_app(uri))
    values = [value for value in result.contents if str(value.uri) == uri and value.mime_type == APP_MIME_TYPE]
    if len(values) != 1:
        raise ServiceError("unavailable", "Expected exactly one MCP App HTML resource")
    value = values[0]
    if isinstance(value, TextResourceContents):
        html = value.text
    elif isinstance(value, BlobResourceContents):
        html = base64.b64decode(value.blob, validate=True).decode("utf-8")
    else:
        raise ServiceError("unavailable", "Unsupported MCP App resource content")
    if len(html.encode()) > MAX_APP_BYTES:
        raise ServiceError("payload_too_large", "MCP App HTML exceeds the resource limit")
    return AppResource(uri=uri, mime_type=APP_MIME_TYPE, html=html, metadata=value.meta or {})


async def _read_resource(runtime: Runtime, actor: Principal, workspace_id: str, saved: AppBinding) -> AppResource:
    resolved = await _resolve(runtime, actor, workspace_id, saved)
    try:
        async with _toolset(runtime, resolved) as toolset:
            return await _resource(toolset, saved.reference.resource_uri)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError("unavailable", "MCP App resource could not be read") from None


async def open_app(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, app_id: str) -> OpenedApp:
    saved = await original(runtime, actor, workspace_id, run_id, app_id)
    resource = await _read_resource(runtime, actor, workspace_id, saved)
    return OpenedApp(
        reference=saved.reference, tool=saved.tool, arguments=saved.arguments, result=saved.result, resource=resource
    )


async def read_resource(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, app_id: str, uri: str
) -> AppResource:
    saved = await _binding(runtime, actor, workspace_id, run_id, app_id)
    if uri != saved.reference.resource_uri:
        raise invalid("uri", "Only the saved App resource can be read")
    return await _read_resource(runtime, actor, workspace_id, saved)


async def call_tool(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, app_id: str, body: AppToolCall
) -> AppToolResult:
    if len(body.model_dump_json().encode()) > MAX_APP_BYTES:
        raise ServiceError("payload_too_large", "MCP App arguments exceed the request limit")
    saved = await _binding(runtime, actor, workspace_id, run_id, app_id)
    resolved = await _resolve(runtime, actor, workspace_id, saved, tool_name=body.name)
    call_id = new_object_id("call")

    async def check(_dispatch: ToolDispatch) -> None:
        nonlocal sent
        # Permission and configuration come from this request's first resolution. A dispatch round
        # only fences the connection version and runs the deployment's admission policy.
        connection = resolved.connection
        async with transaction(runtime.storage) as session:
            version = await session.scalar(
                select(ConnectionRow.version).where(
                    ConnectionRow.id == connection.id,
                    ConnectionRow.workspace_id == connection.workspace_id,
                    ConnectionRow.organization_id == connection.organization_id,
                )
            )
            if version is None:
                raise not_found("connection", connection.id)
            if version != connection.version:
                raise conflict("connection", connection.id, "connection_changed")
            if runtime.admission is not None:
                admit = getattr(runtime.admission, "proceed_app", None)
                if admit is None:
                    raise ServiceError("forbidden", "This deployment does not admit direct MCP App calls")
                await admit(
                    session,
                    AppCallContext(
                        workspace_id, actor.id, run_id, app_id, saved.reference.connection_id, body.name, call_id
                    ),
                )
        sent = True

    sent = False
    try:
        async with _toolset(runtime, resolved) as toolset:
            tools = await toolset.list_tools()
            if len(tools) > DISCOVERY_MAX_TOOLS:
                raise ServiceError("unavailable", "The server lists more tools than discovery reads")
            tool = next((tool for tool in tools if tool.name == body.name), None)
            if tool is None:
                raise ServiceError("forbidden", "The tool is not visible to MCP Apps")
            try:
                require_classic(tool)
            except ToolFailed as error:
                raise invalid("name", str(error)) from None
            result = await call_app_tool(
                toolset, ToolDispatch(saved.reference.connection_id, None, body.name, call_id), check, body.arguments
            )
    except ServiceError:
        raise
    except Exception as error:
        if not sent and isinstance(error, MCPError) and error.code == INVALID_PARAMS:
            raise ServiceError("forbidden", "The tool is not visible to MCP Apps") from None
        logger.warning("MCP App operation failed", extra={"app_id": app_id, "call_id": call_id})
        raise ServiceError(
            "unavailable",
            "MCP App operation failed; it was not retried",
            {
                "reason": "outcome_unknown" if sent else "not_sent",
                "call_id": call_id,
            },
        ) from None
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_APP_BYTES:
        raise ServiceError(
            "payload_too_large",
            "The completed MCP App result exceeds the response limit",
            {
                "call_id": call_id,
                "reason": "completed",
            },
        )
    logger.info("MCP App operation completed", extra={"app_id": app_id, "call_id": call_id, "tool_name": body.name})
    return AppToolResult(result=result)
