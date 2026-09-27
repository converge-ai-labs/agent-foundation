"""One entered Client per Thread/server recipe, independent of Run and viewer lifetime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Self
from uuid import uuid4

from a13n_harness.context import AgentContext
from anyio import CancelScope
from fastmcp import Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.transports import ClientTransport
from mcp.client.extension import advertise
from mcp.types import CallToolResult, ReadResourceResult, Tool
from pydantic_ai import RunContext, ToolDefinition
from pydantic_ai.capabilities import MCP, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import ToolsetTool

from .transport import ObservedTransport

MIME_TYPE = "text/html;profile=mcp-app"
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_PENDING_CALLS = 128
_MAX_PENDING_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class CapturedCall:
    connection: Connection
    tool: Tool
    arguments: dict[str, Any]
    result: CallToolResult
    app_id: str = field(default_factory=lambda: f"app_{uuid4().hex}")


@dataclass
class CallCapture:
    tool: Tool
    captures: Captures
    run_id: str
    call_id: str | None


_capture: ContextVar[CallCapture | None] = ContextVar("mcp_app_capture", default=None)
_return_boundary: ContextVar[tuple[str, str] | None] = ContextVar("mcp_app_return_boundary", default=None)


class AppMCP(MCP[AgentContext]):
    """Associate nested dispatches with the outer, retained native tool return."""

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        current = _return_boundary.get()
        if current is not None and current[0] == ctx.deps.run_id:
            return await handler(args)
        token = _return_boundary.set((ctx.deps.run_id, call.tool_call_id))
        try:
            return await handler(args)
        finally:
            _return_boundary.reset(token)


class AppClient(Client[ClientTransport]):
    """Capture once at the public raw boundary; leave upstream model conversion intact."""

    connection: Connection

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        connection = self.connection
        async with connection.dispatch:
            connection.require_connected()
            result = await super().call_tool_mcp(name, arguments, progress_handler, timeout, meta)
        capture = _capture.get()
        if capture is not None:
            # Presentation failure must never turn a completed business operation into a retry.
            try:
                capture.captures.add(
                    capture.run_id, capture.call_id, CapturedCall(connection, capture.tool, dict(arguments), result)
                )
            except (ValueError, TypeError):
                pass
        return result

    async def call_app_tool(
        self, name: str, arguments: dict[str, Any], *, authorize: Callable[[], Awaitable[None]]
    ) -> CallToolResult:
        """Recheck a View's current authority in the lane, without creating a Run capture."""
        async with self.app_dispatch(authorize):
            return await super().call_tool_mcp(name, arguments)

    async def read_app_resource(self, uri: str, *, authorize: Callable[[], Awaitable[None]]) -> ReadResourceResult:
        async with self.app_dispatch(authorize):
            return await super().read_resource_mcp(uri)

    @asynccontextmanager
    async def app_dispatch(self, authorize: Callable[[], Awaitable[None]]) -> AsyncIterator[None]:
        async with self.connection.dispatch:
            self.connection.require_admission()
            await authorize()
            self.connection.require_admission()
            self.connection._borrows += 1
            try:
                yield
            finally:
                self.connection.release()


class Connection:
    """A dedicated owner enters and exits the Client in the same task."""

    def __init__(
        self, thread_id: str, server_id: str, recipe: str, transport: ClientTransport, binding: str | None = None
    ) -> None:
        self.thread_id = thread_id
        self.server_id = server_id
        self.generation = f"conn_{uuid4().hex}"
        self.recipe = recipe
        self.binding = binding
        self.dispatch = asyncio.Lock()
        self._stop = asyncio.Event()
        self._ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._task: asyncio.Task[None] | None = None
        self.retired = False
        self._closed = False
        self._borrows = 0
        self.client = AppClient(
            ObservedTransport(transport, self._disconnected),
            mode="legacy",
            cache=False,
            init_timeout=30,
            timeout=60,
            extensions=[advertise("io.modelcontextprotocol/ui", {"mimeTypes": [MIME_TYPE]})],
        )
        self.client.connection = self

    @property
    def connected(self) -> bool:
        return not self.retired and self._usable

    @property
    def _usable(self) -> bool:
        return not self._closed and self.client.is_connected() and self._task is not None and not self._task.done()

    def require_connected(self) -> None:
        if not self._usable:
            raise RuntimeError("MCP App connection is closed or disconnected; explicit activation is required.")

    def require_admission(self) -> None:
        self.require_connected()
        if self.retired:
            raise RuntimeError("MCP App binding was retired; explicit activation is required.")

    async def start(self) -> None:
        if self._task is None:
            if self.retired or self._closed:
                raise RuntimeError("MCP App connection was closed before startup; explicit activation is required.")
            self._task = asyncio.create_task(self._own(), name=f"mcp-app:{self.thread_id}:{self.server_id}")
        await asyncio.shield(self._ready)

    async def _own(self) -> None:
        try:
            async with self.client:
                self._ready.set_result(None)
                await self._stop.wait()
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            self._closed = True

    def _disconnected(self) -> None:
        self._closed = True
        self._stop.set()

    def retire(self) -> None:
        """Stop new App admissions; captured Run borrowers finish on their original connection."""
        self.retired = True
        if not self._borrows:
            self._stop.set()

    def release(self) -> None:
        self._borrows -= 1
        if self.retired and not self._borrows:
            self._stop.set()

    async def close(self) -> None:
        self.retired = True
        async with self.dispatch:
            self._closed = True
            self._stop.set()
        if self._task is not None:
            await asyncio.shield(self._task)


class AppToolset(MCPToolset[AgentContext]):
    """Fresh Pydantic adapter borrowing a Host connection without Run-bound handlers."""

    def __init__(self, connection: Connection, captures: Captures) -> None:
        super().__init__(
            connection.client,
            id=connection.server_id,
            cache_tools=False,
            cache_resources=False,
            cache_prompts=False,
            prefer_tasks=False,
        )
        self.connection = connection
        self.captures = captures
        self._entries = 0

    async def __aenter__(self) -> Self:
        # Native direct_call_tool re-enters this same Run adapter on every call.
        if self._entries:
            self.connection.require_connected()
        else:
            self.connection.require_admission()
        self._entries += 1
        self.connection._borrows += 1
        try:
            return await super().__aenter__()
        except BaseException:
            self._entries -= 1
            self.connection.release()
            raise

    async def __aexit__(self, *args: Any) -> bool | None:
        with CancelScope(shield=True):
            try:
                return await super().__aexit__(*args)
            finally:
                self._entries -= 1
                self.connection.release()

    async def list_tools(self) -> list[Tool]:
        tools = await super().list_tools()
        return [tool for tool in tools if "model" in visibility(tool)]

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        declaration = next((item for item in await self.client.list_tools() if item.name == name), None)
        if (tool.tool_def.metadata or {}).get("task"):
            raise ValueError("Task-based tools are not supported by this MCP Apps profile.")
        if declaration is not None:
            require_classic_tool(declaration)
        if declaration is None or resource_uri(declaration) is None:
            return await super().call_tool(name, tool_args, ctx, tool)
        boundary = _return_boundary.get()
        call_id = boundary[1] if boundary is not None and boundary[0] == ctx.deps.run_id else ctx.tool_call_id
        token = _capture.set(CallCapture(declaration, self.captures, ctx.deps.run_id, call_id))
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        finally:
            _capture.reset(token)


class Captures:
    """Short-lived raw data awaiting an actual retained tool-return boundary."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], list[tuple[CapturedCall, int]]] = {}
        self._bytes = 0
        self._count = 0

    def add(self, run_id: str, call_id: str | None, call: CapturedCall) -> None:
        if not call_id:
            return
        size = len(call.result.model_dump_json().encode()) + len(call.tool.model_dump_json().encode())
        size += len(json.dumps(call.arguments).encode())
        if size > _MAX_CAPTURE_BYTES:
            return
        # Bound individual nested calls and total retained bytes, not just outer IDs.
        while self._pending and (self._count >= _MAX_PENDING_CALLS or self._bytes + size > _MAX_PENDING_BYTES):
            self.take(*next(iter(self._pending)))
        self._pending.setdefault((run_id, call_id), []).append((call, size))
        self._bytes += size
        self._count += 1

    def take(self, run_id: str, call_id: str) -> list[CapturedCall]:
        entries = self._pending.pop((run_id, call_id), [])
        self._bytes -= sum(size for _, size in entries)
        self._count -= len(entries)
        return [call for call, _ in entries]

    def discard_run(self, run_id: str) -> None:
        for key in tuple(self._pending):
            if key[0] == run_id:
                self.take(*key)


class Connections:
    """Process-local ownership, with no idle or viewer-based eviction."""

    def __init__(self) -> None:
        self._connections: dict[tuple[str, str], Connection] = {}
        self._lock = asyncio.Lock()
        self._retired: set[Connection] = set()
        self._closed = False
        self.captures = Captures()

    async def acquire(
        self,
        thread_id: str,
        server_id: str,
        recipe: str,
        transport: ClientTransport,
        *,
        binding: str | None = None,
        activate: bool = False,
    ) -> Connection:
        async with self._lock:
            if self._closed:
                raise RuntimeError("MCP App connection registry is closed.")
            key = (thread_id, server_id)
            existing = self._connections.get(key)
            starting = (
                existing is not None and not existing._ready.done() and not existing.retired and not existing._closed
            )
            if existing is not None and existing.recipe == recipe and (existing.connected or starting or not activate):
                if not starting:
                    existing.require_admission()
                connection = existing
            else:
                if existing is not None:
                    existing.retire()
                    self._retired = {item for item in self._retired if item._task is not None and not item._task.done()}
                    self._retired.add(existing)
                connection = Connection(thread_id, server_id, recipe, transport, binding)
                self._connections[key] = connection
        # Only dictionary publication is serialized globally. Different servers may
        # initialize concurrently; callers for one generation share its ready future.
        await connection.start()
        connection.require_admission()
        return connection

    def get(self, thread_id: str, server_id: str) -> Connection | None:
        return self._connections.get((thread_id, server_id))

    def current(self) -> tuple[Connection, ...]:
        return tuple(self._connections.values())

    def retain_bindings(self, bindings: dict[str, str]) -> None:
        """Retire removed or changed configuration without interrupting admitted Runs."""
        for connection in self._connections.values():
            if connection.server_id not in bindings or bindings[connection.server_id] != connection.binding:
                connection.retire()

    def retain_thread_servers(self, thread_id: str, server_ids: set[str]) -> None:
        for connection in self._connections.values():
            if connection.thread_id == thread_id and connection.server_id not in server_ids:
                connection.retire()

    async def close_thread(self, thread_id: str) -> None:
        async with self._lock:
            selected = {item for item in self._retired if item.thread_id == thread_id}
            self._retired.difference_update(selected)
            for key in tuple(self._connections):
                if key[0] == thread_id:
                    selected.add(self._connections.pop(key))
        for connection in selected:
            await connection.close()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            selected = {*self._connections.values(), *self._retired}
            self._connections.clear()
            self._retired.clear()
        await asyncio.gather(*(connection.close() for connection in selected))


def require_classic_tool(tool: Tool) -> None:
    if tool.execution is not None and tool.execution.task_support == "required":
        raise ValueError("Task-based tools are not supported by this MCP Apps profile.")


def resource_uri(tool: Tool) -> str | None:
    ui = (tool.meta or {}).get("ui")
    uri = ui.get("resourceUri") if isinstance(ui, dict) else None
    return uri if isinstance(uri, str) and uri.startswith("ui://") else None


def visibility(tool: Tool) -> list[str]:
    ui = (tool.meta or {}).get("ui")
    value = ui.get("visibility") if isinstance(ui, dict) else None
    return value if isinstance(value, list) else ["model", "app"]
