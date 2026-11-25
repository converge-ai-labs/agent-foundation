"""SEP-2663 client integration for Host-owned tool executions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

from fastmcp.client.client import CallToolResult
from mcp import ClientSession, types
from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from pydantic import AwareDatetime, ConfigDict, Field, JsonValue
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.mcp import _map_mcp_call_tool_result
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
from a13n_harness.mcp_inputs import McpInputResponse, McpTaskInputRequest
from a13n_harness.mcp_task_state import McpTaskExecutionState
from a13n_harness.tool_execution import current_execution, current_execution_runtime, execution_cleanup

TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"
_MAX_LIFETIME = timedelta(hours=24)


def task_server_id(binding: str) -> str:
    return sha256(binding.encode()).hexdigest()


def enable_task_ownership[Deps](tools: dict[str, ToolsetTool[Deps]]) -> dict[str, ToolsetTool[Deps]]:
    return {
        name: replace(
            tool,
            tool_def=replace(
                tool.tool_def,
                metadata={
                    **(tool.tool_def.metadata or {}),
                    TOOL_EXECUTION_METADATA_KEY: "owned",
                },
            ),
        )
        for name, tool in tools.items()
    }


def has_tasks(capabilities: types.ServerCapabilities | None) -> bool:
    return capabilities is not None and TASKS_EXTENSION_ID in (capabilities.extensions or {})


def task_value(result: types.CallToolResult) -> Any:
    if result.is_error:
        raise ToolFailed("MCP task returned a tool error.")
    return _map_mcp_call_tool_result(CallToolResult(result.content, result.structured_content, result.meta))


class TaskHandle(types.Result):
    result_type: Literal["task"] = Field(alias="resultType")
    task_id: str = Field(alias="taskId", min_length=1)
    status: Literal["working", "input_required", "completed", "failed", "cancelled"]
    created_at: AwareDatetime = Field(alias="createdAt")
    ttl_ms: int | None = Field(default=None, alias="ttlMs", ge=0)
    status_message: str | None = Field(default=None, alias="statusMessage")
    poll_interval_ms: int | None = Field(default=None, alias="pollIntervalMs", ge=0)

    def checkpoint(self, connection_id: str, server_id: str, tool_name: str) -> McpTaskExecutionState:
        now = datetime.now(UTC)
        expires = now + _MAX_LIFETIME
        if self.ttl_ms is not None:
            ttl = min(self.ttl_ms, int(_MAX_LIFETIME.total_seconds() * 1000))
            expires = min(expires, self.created_at + timedelta(milliseconds=ttl))
        interval = max(0.25, self.poll_interval_ms / 1000) if self.poll_interval_ms is not None else 1.0
        return McpTaskExecutionState(
            connection_id=connection_id,
            tool_name=tool_name,
            server_id=server_id,
            mcp_task_id=self.task_id,
            # Even a terminal admission response needs a tasks/get to obtain its outcome.
            status="working",
            status_message=self.status_message,
            expires_at=expires,
            next_poll_at=now,
            poll_interval_seconds=interval,
        )


class TaskSnapshot(types.Result):
    result_type: Literal["complete"] = Field(alias="resultType")
    task_id: str = Field(alias="taskId")
    status: Literal["working", "input_required", "completed", "failed", "cancelled"]
    status_message: str | None = Field(default=None, alias="statusMessage")
    poll_interval_ms: int | None = Field(default=None, alias="pollIntervalMs", ge=0)
    input_requests: dict[str, types.InputRequest] | None = Field(default=None, alias="inputRequests")
    result: dict[str, JsonValue] | None = None

    def checkpoint(self, previous: McpTaskExecutionState) -> McpTaskExecutionState:
        if self.task_id != previous.mcp_task_id:
            raise ToolFailed("MCP server returned a different task ID.")
        interval = (
            previous.poll_interval_seconds if self.poll_interval_ms is None else max(0.25, self.poll_interval_ms / 1000)
        )
        inputs = None
        if self.status == "input_required":
            inputs = {
                key: McpTaskInputRequest.model_validate(
                    {
                        **request.params.model_dump(mode="json", by_alias=False),
                        "schema": (
                            request.params.requested_schema
                            if isinstance(request.params, types.ElicitRequestFormParams)
                            else None
                        ),
                        "expires_at": previous.expires_at,
                    }
                )
                for key, request in (self.input_requests or {}).items()
                if isinstance(request, types.ElicitRequest) and key not in previous.answered_inputs
            }
            if not inputs:
                if not set(self.input_requests or {}) & previous.answered_inputs:
                    raise ToolFailed("MCP task did not provide a supported input request.")
                # Acknowledged keys can remain observable while the server applies the update.
                return previous.model_copy(
                    update={
                        "status": "working",
                        "input_required": None,
                        "next_poll_at": datetime.now(UTC) + timedelta(seconds=interval),
                    }
                )
        return previous.model_copy(
            update={
                "status": self.status,
                "status_message": self.status_message,
                "poll_interval_seconds": interval,
                "next_poll_at": datetime.now(UTC) + timedelta(seconds=interval) if self.status == "working" else None,
                "input_required": inputs,
            }
        )

    def outcome(self) -> types.CallToolResult:
        if self.status == "completed" and self.result is not None:
            return types.CallToolResult.model_validate(self.result)
        raise ToolFailed("MCP task was cancelled by the server." if self.status == "cancelled" else "MCP task failed.")


class TaskUpdateResult(types.Result):
    """An acknowledgement, optionally carrying a complete task snapshot."""

    model_config = ConfigDict(extra="allow")

    status: Literal["working", "input_required", "completed", "failed", "cancelled"] | None = None


class TaskParams(types.RequestParams):
    task_id: str = Field(serialization_alias="taskId")


class TaskUpdateParams(TaskParams):
    input_responses: dict[str, JsonValue] = Field(serialization_alias="inputResponses")


class GetTask(types.Request[TaskParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    name_param = "taskId"
    params: TaskParams


class CancelTask(types.Request[TaskParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    name_param = "taskId"
    params: TaskParams


class UpdateTask(types.Request[TaskUpdateParams, Literal["tasks/update"]]):
    method: Literal["tasks/update"] = "tasks/update"
    name_param = "taskId"
    params: TaskUpdateParams


async def get_mcp_task(session: ClientSession, state: McpTaskExecutionState) -> TaskSnapshot:
    return await session.send_request(
        GetTask(params=TaskParams(task_id=state.mcp_task_id)), TaskSnapshot, request_read_timeout_seconds=10
    )


async def cancel_mcp_task(session: ClientSession, state: McpTaskExecutionState) -> None:
    with execution_cleanup():
        await session.send_request(
            CancelTask(params=TaskParams(task_id=state.mcp_task_id)), types.Result, request_read_timeout_seconds=5
        )


async def update_mcp_task(
    session: ClientSession, state: McpTaskExecutionState, responses: dict[str, McpInputResponse]
) -> tuple[McpTaskExecutionState, TaskSnapshot | None]:
    """Send validated HITL answers once; caller owns fencing and publication."""
    if state.status != "input_required" or datetime.now(UTC) >= state.expires_at:
        raise ValueError("MCP task is not awaiting input.")
    pending = dict(state.input_required or {})
    if not responses or set(responses) - pending.keys():
        raise ValueError("MCP input is no longer pending.")
    wire: dict[str, JsonValue] = {}
    for key, response in responses.items():
        pending.pop(key).validate_response(response)
        wire[key] = response.model_dump(mode="json", exclude_none=True)
    result = await session.send_request(
        UpdateTask(params=TaskUpdateParams(task_id=state.mcp_task_id, input_responses=wire)),
        TaskUpdateResult,
        request_read_timeout_seconds=10,
    )
    state = state.model_copy(
        update={
            "status": "input_required" if pending else "working",
            "input_required": pending or None,
            "answered_inputs": state.answered_inputs.union(responses),
            "next_poll_at": datetime.now(UTC),
        }
    )
    if result.status is None:
        return state, None
    snapshot = TaskSnapshot.model_validate(result.model_dump(mode="json", by_alias=True))
    return snapshot.checkpoint(state), snapshot


type TaskInputHandler = Callable[[McpTaskExecutionState], Awaitable[dict[str, McpInputResponse]]]


async def supervise_mcp_task(
    session: ClientSession, state: McpTaskExecutionState, *, input_handler: TaskInputHandler | None = None
) -> types.CallToolResult:
    runtime, record = current_execution_runtime(), current_execution()
    try:
        if runtime is not None:
            await runtime.checkpoint_mcp_task(state)
        if runtime is not None and record is not None and runtime.cancellation_requested(record.execution_id):
            raise asyncio.CancelledError
        while (now := datetime.now(UTC)) < state.expires_at:
            if state.status == "input_required" and input_handler is not None:
                responses = await input_handler(state)
                if datetime.now(UTC) >= state.expires_at:
                    break
                state, snapshot = await update_mcp_task(session, state, responses)
            else:
                await asyncio.sleep(max(0, (state.due_at - now).total_seconds()))
                if datetime.now(UTC) >= state.expires_at:
                    break
                snapshot = await get_mcp_task(session, state)
                state = snapshot.checkpoint(state)
            if state.status not in {"working", "input_required"}:
                assert snapshot is not None
                return snapshot.outcome()
            if runtime is not None:
                if state.status == "input_required" and input_handler is None:
                    # The control owner must apply its input policy after the foreground handoff.
                    state = state.model_copy(update={"next_poll_at": datetime.now(UTC)})
                await runtime.checkpoint_mcp_task(state)
                if state.status == "input_required" and input_handler is None:
                    await asyncio.sleep(max(0, (state.expires_at - datetime.now(UTC)).total_seconds()))
                    break
            elif state.status == "input_required":
                raise ToolFailed("MCP task requires a Host-owned execution to receive input.")
        raise ToolFailed("MCP task expired.")
    except asyncio.CancelledError:
        if runtime is None or record is None or not runtime.yielding(record.execution_id):
            try:
                await cancel_mcp_task(session, state)
            except Exception:
                pass  # Remote cancellation is cooperative and time bounded.
        raise


class DurableTasks(ClientExtension):
    """Resolve an extension claim without invoking FastMCP's in-memory task driver."""

    identifier = TASKS_EXTENSION_ID

    def __init__(
        self, connection_id: str, *, server_id: str | None = None, input_handler: TaskInputHandler | None = None
    ) -> None:
        self.connection_id = connection_id
        self.server_id = server_id or connection_id
        self.input_handler = input_handler

    def claims(self) -> Sequence[ResultClaim[Any]]:
        return (
            ResultClaim(
                result_type="task",
                model=TaskHandle,
                resolve=self.resolve,
                protocol_versions=frozenset({"2026-07-28"}),
            ),
        )

    async def resolve(self, handle: TaskHandle, ctx: ClaimContext) -> types.CallToolResult:
        return await supervise_mcp_task(
            ctx.session,
            handle.checkpoint(self.connection_id, self.server_id, ctx.tool_name),
            input_handler=self.input_handler,
        )
