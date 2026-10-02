"""Process-local MCP ownership independent of an Agent Run or browser View."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from uuid import uuid4

from a13n_logging import get_logger
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.transports import ClientTransport
from mcp.client.extension import ClientExtension
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ElicitRequestParams,
    GetPromptRequest,
    GetPromptResult,
    ReadResourceRequest,
    ReadResourceResult,
)

from .transports import ObservedTransport


@dataclass(frozen=True)
class Operation:
    run_id: str | None = None
    tool_call_id: str | None = None
    view_id: str | None = None


operation_context: ContextVar[Operation | None] = ContextVar("mcp_operation", default=None)


class InputHandler(Protocol):
    async def __call__(
        self, connection: Connection, operation: Operation, params: ElicitRequestParams
    ) -> ElicitResult[Any]: ...


class HostClient(Client[ClientTransport]):
    """One dispatch lane and stable Host callbacks for all consumers of a client."""

    connection: Connection

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        async with self.connection.operation():
            return await super().call_tool_mcp(name, arguments, progress_handler, timeout, meta)

    async def read_resource_mcp(self, uri: Any, meta: dict[str, Any] | None = None) -> ReadResourceResult:
        async with self.connection.operation():
            return await super().read_resource_mcp(uri, meta)

    async def get_prompt_mcp(
        self, name: str, arguments: dict[str, Any] | None = None, meta: dict[str, Any] | None = None
    ) -> GetPromptResult:
        async with self.connection.operation():
            return await super().get_prompt_mcp(name, arguments, meta)

    async def call_app_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        authorize: Callable[[], Awaitable[None]],
        operation: Operation | None = None,
    ) -> CallToolResult:
        async with self.app_dispatch(authorize, operation):
            return await super().call_tool_mcp(name, arguments)

    async def read_app_resource(
        self, uri: str, *, authorize: Callable[[], Awaitable[None]], operation: Operation | None = None
    ) -> ReadResourceResult:
        async with self.app_dispatch(authorize, operation):
            return await super().read_resource_mcp(uri)

    @asynccontextmanager
    async def app_dispatch(
        self, authorize: Callable[[], Awaitable[None]], operation: Operation | None = None
    ) -> AsyncIterator[None]:
        async with self.connection.operation(authorize=authorize, operation=operation):
            self.connection.require_admission()
            self.connection._borrows += 1
            try:
                yield
            finally:
                self.connection.release()


class Connection:
    """A dedicated task enters/exits the SDK client while callers borrow projections."""

    def __init__(
        self,
        thread_id: str,
        server_id: str,
        recipe: str,
        transport: ClientTransport,
        binding: str | None = None,
        *,
        protocol: str = "auto",
        client_factory: type[HostClient] = HostClient,
        extensions: Sequence[ClientExtension] = (),
        input_handler: InputHandler | None = None,
        init_timeout: int = 30,
        cleanup_timeout_seconds: float = 10,
    ) -> None:
        self.thread_id, self.server_id = thread_id, server_id
        self.generation = f"conn_{uuid4().hex}"
        self.recipe, self.binding = recipe, binding
        self.dispatch = asyncio.Lock()
        self._stop = asyncio.Event()
        self._ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._task: asyncio.Task[None] | None = None
        self._active_task: asyncio.Task[Any] | None = None
        self._active_operation: Operation | None = None
        self._authorize: Callable[[], Awaitable[None]] | None = None
        self._input_handler = input_handler
        self._cleanup_timeout = cleanup_timeout_seconds
        self.retired = False
        self._closed = False
        self._borrows = 0
        self.client = client_factory(
            ObservedTransport(transport, self._disconnected, self._before_request),
            mode=protocol,
            cache=False,
            init_timeout=init_timeout,
            # Legacy server requests share the business-request deadline. Leave
            # room for the Host's five-minute human-input window and final round.
            timeout=360 if input_handler is not None else 60,
            extensions=extensions,
            elicitation_handler=self._elicit if input_handler is not None else None,
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
            raise RuntimeError("MCP connection is closed or disconnected; explicit activation is required.")

    def require_admission(self) -> None:
        self.require_connected()
        if self.retired:
            raise RuntimeError("MCP binding was retired; explicit activation is required.")

    @asynccontextmanager
    async def operation(
        self, *, authorize: Callable[[], Awaitable[None]] | None = None, operation: Operation | None = None
    ) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is self._active_task:
            # An App authorization scope can call a normal SDK tool method without
            # taking the same lane twice. Other tasks cannot inherit this ownership.
            yield
            return
        async with self.dispatch:
            self.require_connected()
            self._active_task, self._active_operation = task, operation or operation_context.get() or Operation()
            self._authorize = authorize
            try:
                if authorize is not None:
                    self.require_admission()
                    await authorize()
                    self.require_admission()
                yield
            finally:
                self._active_task = None
                self._active_operation = None
                self._authorize = None

    async def _before_request(self, request: Any) -> None:
        if not isinstance(request, (CallToolRequest, ReadResourceRequest, GetPromptRequest)):
            return
        if self._active_operation is None:
            return
        self.require_connected()
        if request.params.input_responses is not None or request.params.request_state is not None:
            self.require_admission()
        if self._authorize is not None:
            await self._authorize()
            self.require_admission()

    async def _elicit(
        self, message: str, response_type: Any, params: ElicitRequestParams, context: Any
    ) -> ElicitResult[Any]:
        del message, response_type, context
        # Legacy requests run on the dispatcher task; caller ContextVars alone
        # cannot route them. The lane supplies one unambiguous trusted operation.
        operation, handler = self._active_operation, self._input_handler
        if operation is None or handler is None:
            return ElicitResult(action="decline")
        self.require_admission()
        if self._authorize is not None:
            await self._authorize()
        response = await handler(self, operation, params)
        self.require_admission()
        if self._authorize is not None:
            await self._authorize()
        return response

    async def start(self) -> None:
        if self._task is None:
            if self.retired or self._closed:
                raise RuntimeError("MCP connection was closed before startup; explicit activation is required.")
            self._task = asyncio.create_task(self._own(), name=f"mcp:{self.thread_id}:{self.server_id}")
        await asyncio.shield(self._ready)

    async def _own(self) -> None:
        try:
            async with self.client:
                if self._closed:
                    return
                self._ready.set_result(None)
                await self._stop.wait()
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            self._closed = True
            if not self._ready.done():
                self._ready.set_exception(RuntimeError("MCP connection closed during startup."))
                # Startup may no longer have a waiter after caller cancellation.
                self._ready.exception()

    def _disconnected(self) -> None:
        self._closed = True
        self._stop.set()

    def retire(self) -> None:
        self.retired = True
        if not self._borrows:
            self._stop.set()

    def release(self) -> None:
        self._borrows -= 1
        if self.retired and not self._borrows:
            self._stop.set()

    async def close(self) -> None:
        self.retired = True
        self._closed = True
        if not self._ready.done():
            self._ready.set_exception(RuntimeError("MCP connection closed during startup."))
            self._ready.exception()
        tasks = {
            task for task in (self._active_task, self._task) if task is not None and task is not asyncio.current_task()
        }
        active = self._active_task
        if active is not None and active in tasks:
            active.cancel()
        self._stop.set()
        # asyncio.wait bounds even cancellation-resistant collaborators; wait_for
        # would wait indefinitely for their cancellation acknowledgement.
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._cleanup_timeout)
            for task in pending:
                task.cancel()
            if pending:
                get_logger(__name__).warning("MCP cleanup exceeded its deadline; remote outcome may be unknown.")


class Connections:
    """Host client registry, with no idle or viewer-based eviction."""

    def __init__(
        self,
        *,
        client_factory: type[HostClient] = HostClient,
        extensions: Sequence[ClientExtension] = (),
        input_handler: InputHandler | None = None,
        cleanup_timeout_seconds: float = 10,
    ) -> None:
        self._cleanup_timeout = cleanup_timeout_seconds
        self._connections: dict[tuple[str, str], Connection] = {}
        self._lock = asyncio.Lock()
        self._retired: set[Connection] = set()
        self._closed = False
        self._client_factory, self._extensions, self._input_handler = client_factory, extensions, input_handler

    @property
    def cleanup_timeout_seconds(self) -> float:
        return self._cleanup_timeout

    @property
    def input_handler(self) -> InputHandler | None:
        return self._input_handler

    async def acquire(
        self,
        thread_id: str,
        server_id: str,
        recipe: str,
        transport: ClientTransport,
        *,
        binding: str | None = None,
        activate: bool = False,
        protocol: str = "auto",
        client_factory: type[HostClient] | None = None,
        extensions: Sequence[ClientExtension] | None = None,
    ) -> Connection:
        async with self._lock:
            if self._closed:
                raise RuntimeError("MCP connection registry is closed.")
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
                connection = Connection(
                    thread_id,
                    server_id,
                    recipe,
                    transport,
                    binding,
                    protocol=protocol,
                    client_factory=client_factory or self._client_factory,
                    extensions=self._extensions if extensions is None else extensions,
                    input_handler=self._input_handler,
                    cleanup_timeout_seconds=self._cleanup_timeout,
                )
                self._connections[key] = connection
        await connection.start()
        connection.require_admission()
        return connection

    def get(self, thread_id: str, server_id: str) -> Connection | None:
        return self._connections.get((thread_id, server_id))

    def current(self) -> tuple[Connection, ...]:
        return tuple(self._connections.values())

    def retain_bindings(self, bindings: dict[str, str]) -> None:
        for connection in self._connections.values():
            if connection.server_id not in bindings or bindings[connection.server_id] != connection.binding:
                connection.retire()

    def retain_thread_servers(self, thread_id: str, server_ids: set[str]) -> None:
        for connection in self._connections.values():
            if connection.thread_id == thread_id and connection.server_id not in server_ids:
                connection.retire()

    async def close_integration(self, thread_id: str, server_id: str) -> None:
        connection = self.get(thread_id, server_id)
        if connection is not None:
            await connection.close()

    async def close_thread(self, thread_id: str) -> None:
        async with self._lock:
            selected = {item for item in self._retired if item.thread_id == thread_id}
            self._retired.difference_update(selected)
            for key in tuple(self._connections):
                if key[0] == thread_id:
                    selected.add(self._connections.pop(key))
        await asyncio.gather(*(connection.close() for connection in selected))

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            selected = {*self._connections.values(), *self._retired}
            self._connections.clear()
            self._retired.clear()
        await asyncio.gather(*(connection.close() for connection in selected))
