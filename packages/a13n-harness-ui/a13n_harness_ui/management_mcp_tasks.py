"""SEP-2663 polling for Runs started through the owner management surface."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from fastmcp import Context
from fastmcp.server.extensions import MethodBinding, ServerExtension, ToolCallContinuation, ToolCallOutcome
from fastmcp.tools.base import ToolResult
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CLIENT_CAPABILITIES_META_KEY,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    CallToolRequestParams,
    CallToolResult,
    ErrorData,
    RequestParams,
    TextContent,
)
from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from a13n_harness_ui.storage.repositories import RetainedThreadRun
from a13n_harness_ui.thread_capability import ThreadToolController

_EXTENSION = "io.modelcontextprotocol/tasks"
_PROTOCOL = "2026-07-28"
_SUSPENDED = "Open the Thread in Harness UI WebUI to resolve the pending request. Resuming starts a new Run."


class _TaskParams(RequestParams):
    task_id: str = Field(alias="taskId", min_length=1, max_length=80)


class _TaskResult(BaseModel):
    # The SDK's mcp.types.Task is for the older, experimental protocol.
    resultType: Literal["task", "complete"]
    taskId: str
    status: Literal["working", "completed", "failed", "cancelled"]
    createdAt: datetime
    lastUpdatedAt: datetime
    pollIntervalMs: int | None = None
    statusMessage: str | None = None
    result: CallToolResult | None = None
    error: ErrorData | None = None

    @model_serializer(mode="wrap")
    def _with_ttl(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # The SDK dumps with exclude_none=True, but SEP-2663 requires ttlMs.
        return {**handler(self), "ttlMs": None}


def requests_run_task(context: Context) -> bool:
    request = context.request_context
    if request is None or request.protocol_version != _PROTOCOL or request.meta is None:
        return False
    capabilities = request.meta.get(CLIENT_CAPABILITIES_META_KEY)
    extensions = capabilities.get("extensions") if isinstance(capabilities, Mapping) else None
    return isinstance(extensions, Mapping) and isinstance(extensions.get(_EXTENSION), Mapping)


class ManagementRunTasks(ServerExtension):
    identifier = _EXTENSION

    def __init__(self, controller: Callable[[], ThreadToolController]) -> None:
        self._controller = controller

    def methods(self) -> Sequence[MethodBinding]:
        modern = frozenset({_PROTOCOL})
        return (
            MethodBinding("tasks/get", _TaskParams, self._get, modern),
            MethodBinding("tasks/cancel", _TaskParams, self._cancel, modern),
        )

    async def intercept_tool_call(
        self, params: CallToolRequestParams, context: Context, call_next: ToolCallContinuation
    ) -> ToolCallOutcome:
        result = await call_next()
        if (
            params.name not in {"create_thread", "send_thread_message"}
            or not requests_run_task(context)
            or not isinstance(result, ToolResult)
            or not result.structured_content
        ):
            return result
        receipt = result.structured_content.get("receipt")
        receipt_id = receipt.get("receipt_id") if isinstance(receipt, dict) else None
        if result.structured_content.get("ok") is not True or not isinstance(receipt_id, str):
            return result
        run = await self._controller().admitted_run(receipt_id)
        return _task_result(run, seed=True) if run is not None else result

    async def _resolve(self, context: ServerRequestContext[Any, Any], task_id: str) -> RetainedThreadRun:
        if self.client_settings(context) is None:
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message="Missing required client capability",
                data={"requiredCapabilities": {"extensions": {_EXTENSION: {}}}},
            )
        if context.request is not None and context.request.headers.get("mcp-name") != task_id:
            raise MCPError(code=INVALID_PARAMS, message="Mcp-Name must match taskId.")
        run = await self._controller().get_retained_run(task_id)
        if run is None:
            raise MCPError(code=INVALID_PARAMS, message="Unknown task ID.")
        return run

    async def _get(self, context: ServerRequestContext[Any, Any], params: _TaskParams) -> _TaskResult:
        return _task_result(await self._resolve(context, params.task_id))

    async def _cancel(self, context: ServerRequestContext[Any, Any], params: _TaskParams) -> dict[str, str]:
        run = await self._resolve(context, params.task_id)
        await self._controller().cancel_retained_run(run)
        return {"resultType": "complete"}


def _task_result(run: RetainedThreadRun, *, seed: bool = False) -> _TaskResult:
    execution = run.execution
    run_id = execution.run_id
    assert run_id is not None  # The history row is keyed by this Run ID.
    result = _TaskResult(
        resultType="task" if seed else "complete",
        taskId=run_id,
        status="working",
        createdAt=execution.submitted_at,
        lastUpdatedAt=execution.completed_at or execution.started_at or execution.submitted_at,
    )
    if execution.status in {"preparing", "running"}:
        result.pollIntervalMs = 1000
    elif execution.status == "cancelled":
        result.status = "cancelled"
    elif execution.status == "unknown":
        result.status = "failed"
        result.statusMessage = "The Run ended without a saved outcome. Inspect the Thread for recovery status."
        if not seed:
            result.error = ErrorData(
                code=INTERNAL_ERROR, message=result.statusMessage, data={"thread_id": run.thread_id}
            )
    else:
        result.status = "completed"
        if not seed:
            failed = execution.status == "failed"
            payload: dict[str, Any] = {"ok": not failed, "thread_id": run.thread_id, "status": execution.status}
            if failed:
                payload["message"] = "Run execution failed. Inspect the Thread for details."
            elif execution.status == "suspended":
                result.statusMessage = _SUSPENDED
                payload["message"] = _SUSPENDED
            elif run.output is not None:
                payload.update(run.output.model_dump(mode="json"))
                if run.output.output_omitted:
                    payload["message"] = "Output is outside the inline result limit. Inspect the saved Thread history."
            result.result = CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
                structured_content=payload,
                is_error=failed,
            )
    return result
