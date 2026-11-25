"""Process-local human decisions; answering never resumes or admits an Agent Run."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from a13n_harness.mcp_inputs import McpInputResponse as McpInputResponse
from a13n_harness.mcp_inputs import McpTaskInputRequest
from a13n_stream_protocol.interactions import McpElicitation
from fastmcp.client.elicitation import ElicitResult
from mcp.types import ElicitRequestFormParams, ElicitRequestParams
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import HarnessUiError

from .connections import Connection, Operation


class InputModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class McpInputRequestView(McpElicitation):
    view_id: str | None = None
    state: Literal["pending", "accepted", "declined", "cancelled", "expired", "unavailable"] = "pending"


class McpIntegrationView(InputModel):
    thread_id: str
    server_id: str
    generation: str
    connected: bool
    retired: bool


@dataclass
class _Request:
    view: McpInputRequestView
    connection: Connection
    operation: Operation
    prompt: McpTaskInputRequest
    future: asyncio.Future[McpInputResponse]
    response: McpInputResponse | None = None


class Inputs:
    def __init__(self, changed: Callable[[str], Awaitable[None]], *, timeout_seconds: float = 300) -> None:
        self._changed = changed
        self._timeout = timeout_seconds
        self._requests: dict[str, _Request] = {}
        self._closed = False

    async def __call__(
        self, connection: Connection, operation: Operation, params: ElicitRequestParams
    ) -> ElicitResult[Any]:
        form = isinstance(params, ElicitRequestFormParams)
        prompt = McpTaskInputRequest(
            mode=params.mode,
            message=params.message,
            schema=params.requested_schema if form else None,
            url=None if form else params.url,
        )
        response = await self.collect(connection, operation, prompt)
        return ElicitResult(action=response.action, content=response.content)

    async def collect(
        self, connection: Connection, operation: Operation, prompt: McpTaskInputRequest
    ) -> McpInputResponse:
        if self._closed:
            return McpInputResponse(action="cancel")
        # Bounded reconciliation history never evicts a pending operation.
        for key in tuple(self._requests):
            if len(self._requests) < 128:
                break
            if self._requests[key].view.state != "pending":
                self._requests.pop(key)
        if len(self._requests) >= 128:
            return McpInputResponse(action="decline")
        current = datetime.now(UTC)
        expires = (
            min(prompt.expires_at, current + timedelta(seconds=self._timeout))
            if prompt.expires_at
            else (current + timedelta(seconds=self._timeout))
        )
        try:
            view = McpInputRequestView.project(
                prompt,
                request_id=f"mcpinp_{uuid4().hex}",
                thread_id=connection.thread_id,
                server_id=connection.server_id,
                run_id=operation.run_id,
                tool_call_id=operation.tool_call_id,
                expires_at=expires,
            ).model_copy(update={"view_id": operation.view_id})
        except ValueError:
            return McpInputResponse(action="decline")
        request = _Request(view, connection, operation, prompt, asyncio.get_running_loop().create_future())
        self._requests[view.request_id] = request
        try:
            await self._changed(connection.thread_id)
            async with asyncio.timeout(max(0, (expires - datetime.now(UTC)).total_seconds())):
                return await asyncio.shield(request.future)
        except TimeoutError:
            request.view = request.view.model_copy(update={"state": "expired"})
            return McpInputResponse(action="cancel")
        finally:
            if request.view.state == "pending":
                request.view = request.view.model_copy(update={"state": "unavailable"})
            if not request.future.done():
                request.future.cancel()
            await self._changed(connection.thread_id)

    def thread_ids(self) -> set[str]:
        return {item.view.thread_id for item in self._requests.values()}

    def requests(self, thread_ids: set[str]) -> tuple[McpInputRequestView, ...]:
        return tuple(
            item.view.model_copy(deep=True) for item in self._requests.values() if item.view.thread_id in thread_ids
        )

    async def respond(self, thread_ids: set[str], request_id: str, response: McpInputResponse) -> McpInputRequestView:
        request = self._requests.get(request_id)
        if request is None or request.view.thread_id not in thread_ids:
            raise HarnessUiError("MCP input request is unavailable.", code="mcp_input_not_found")
        if request.response is not None:
            if request.response == response:
                return request.view.model_copy(deep=True)
            raise HarnessUiError("MCP input was already answered differently.", code="mcp_input_conflict")
        if request.view.state == "pending" and datetime.now(UTC) >= request.view.expires_at:
            request.view = request.view.model_copy(update={"state": "expired"})
            if not request.future.done():
                request.future.set_result(McpInputResponse(action="cancel"))
            await self._changed(request.view.thread_id)
        if request.view.state != "pending" or request.future.done():
            raise HarnessUiError("MCP input is no longer pending.", code="mcp_input_stale")
        request.connection.require_admission()
        if request.connection._active_operation is not request.operation:
            raise HarnessUiError("The MCP operation is unavailable.", code="mcp_input_stale")
        try:
            request.prompt.validate_response(response)
        except ValueError as exc:
            raise HarnessUiError(str(exc), code="mcp_input_invalid") from exc
        request.response = response.model_copy(deep=True)
        request.view = request.view.model_copy(
            update={"state": {"accept": "accepted", "decline": "declined", "cancel": "cancelled"}[response.action]}
        )
        request.future.set_result(request.response)
        await self._changed(request.view.thread_id)
        return request.view.model_copy(deep=True)

    async def close(self) -> None:
        self._closed = True
        for request in self._requests.values():
            if request.view.state == "pending":
                request.view = request.view.model_copy(update={"state": "unavailable"})
                if not request.future.done():
                    request.future.set_result(McpInputResponse(action="cancel"))
